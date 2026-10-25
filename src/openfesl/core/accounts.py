import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from openfesl.core.entitlements import EntitlementStore, KeyRefusal
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game, Games
from openfesl.core.logins import INVALID_PASSWORD, UNKNOWN_USER, Logins
from openfesl.core.policies import ConcurrentLogin, GamePolicies, Policies
from openfesl.core.registration import (
    INVALID_VALUE,
    TOO_LONG,
    Terms,
    birth_date_error,
    countries,
    is_country,
)
from openfesl.core.texts import language_of
from openfesl.core.verification import EmailVerification, Link, new_link
from openfesl.protocol.acct import (
    Country,
    GetCountryListAnswer,
    NuAddAccount,
    NuEntitleGame,
    NuGetTos,
    NuGetTosAnswer,
    NuLogin,
    NuLoginAnswer,
)
from openfesl.protocol.fesl import FieldError
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)

NOT_ENTITLED = 120
ALREADY_REGISTERED = 160
TERMS_OUT_OF_DATE = 260

MINIMUM_PASSWORD = 8
# The SDK keeps 31 bytes of a password and logs in again after registration with what it kept.
MAXIMUM_PASSWORD = 31
MAXIMUM_NAME = 63

# Enough to tell an address that mail can be sent to from a name that is none.
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# No account here belongs to a console identity, and the service hands out no tokens for other
# services; the SDK passes these errors on to the game.
CONSOLE_LOGINS = ("NuPS3Login", "NuXBL360Login", "NuGetAccountByPS3Ticket")
NOT_OFFERED = ("NuPS3AddAccount", "NuXBL360AddAccount", "GameSpyPreAuth", "NuCreateEncryptedToken")


class AccountState(StrEnum):
    """Whether an account may log in, as the admin decided."""

    ACTIVE = "active"
    DISABLED = "disabled"
    BANNED = "banned"
    DEACTIVATED = "deactivated"
    """Taken out of use at its player's request."""


# The NuLogin errors whose texts in the game's login form name each state.
STATE_ERRORS = {AccountState.DISABLED: 102, AccountState.BANNED: 103, AccountState.DEACTIVATED: 250}


@dataclass(frozen=True, slots=True)
class Account:
    """An account as the services see it.

    Attributes:
        id: The account's number, which the game knows as its user id.
        nuid: The name the account logs in with.
        country: The ISO code chosen on registration.
        terms_version: The version of the terms the account accepted last.
        server: Whether the account belongs to a game's dedicated servers.
        state: Whether the admin lets it log in.
        state_until: When a state other than active ends, if it does.
        verification_expires_at: When the link that confirms its e-mail address expires, while
            it waits for that; None once confirmed or when it never had to be.
    """

    id: int
    nuid: str
    country: str
    terms_version: str | None
    server: bool
    state: AccountState
    state_until: datetime | None
    verification_expires_at: datetime | None

    def login_error(self) -> int | None:
        """Return the NuLogin error that the account's state refuses its logins with, if any."""
        if self.state_until is not None and self.state_until <= datetime.now(UTC):
            return None
        return STATE_ERRORS.get(self.state)


@dataclass(frozen=True, slots=True)
class Credentials:
    """An account with the hash its password is checked against.

    Server accounts have none, and neither has a login that a remembered token proved.
    """

    account: Account
    password_hash: str | None


class AccountStore(Protocol):
    """Where accounts and their remembered logins are kept."""

    async def find(self, nuid: str) -> Credentials | None:
        """Return the account that logs in with a name, matched regardless of case.

        Args:
            nuid: The login name.
        """

    async def find_remembered(self, token_hash: bytes) -> Account | None:
        """Return the account that a remembered login belongs to.

        Args:
            token_hash: The SHA-256 digest of the token the game kept.
        """

    async def add(
        self,
        nuid: str,
        password_hash: str | None,
        country: str,
        terms_version: str | None,
        *,
        server: bool = False,
        verification: Link | None = None,
    ) -> Account | None:
        """Create an account, or return None when the name is taken.

        Args:
            nuid: The login name.
            password_hash: The password's hash; None for a server account.
            country: The ISO code chosen on registration.
            terms_version: The version of the terms accepted on registration, if current.
            server: Whether the account belongs to a game's dedicated servers.
            verification: The link that must confirm its e-mail address before it logs in.
        """

    async def accept_terms(self, account_id: int, version: str) -> None:
        """Record that an account accepted a version of the terms.

        Args:
            account_id: The account.
            version: The version it accepted.
        """

    async def remember(self, account_id: int, token_hash: bytes, password_hash: str) -> bool:
        """Keep a login token so the game can log in without the password later.

        Args:
            account_id: The account.
            token_hash: The SHA-256 digest of the token handed to the game.
            password_hash: The hash the login's password was checked against.

        Returns:
            Whether the token was kept, which it is not once the password changed.
        """

    async def change_password(self, account_id: int, password_hash: str) -> None:
        """Replace an account's password and forget every login remembered with the old one.

        Args:
            account_id: The account.
            password_hash: The new password's hash.
        """

    async def change_country(self, account_id: int, country: str) -> None:
        """Replace the country an account chose.

        Args:
            account_id: The account.
            country: The ISO code of the new country.
        """

    async def set_state(
        self, account_id: int, state: AccountState, until: datetime | None, admin: str | None
    ) -> None:
        """Change whether an account may log in and record the change among the admin's actions.

        Args:
            account_id: The account.
            state: Its new state.
            until: When the state ends, if it does.
            admin: The admin who changed it; None for the command line.
        """


class AccountService:
    """The acct transactions that register accounts and log them in."""

    def __init__(
        self,
        store: AccountStore,
        logins: Logins,
        entitlements: EntitlementStore,
        terms: Terms,
        policies: GamePolicies,
        verification: EmailVerification,
    ) -> None:
        """Serve accounts from a store.

        Args:
            store: Where accounts are kept.
            logins: Checks passwords and remembered logins.
            entitlements: Where the entitlements that let players in are kept.
            terms: The service's terms.
            policies: Each game's policies.
            verification: Confirms the e-mail addresses of new accounts.
        """
        self._store = store
        self._logins = logins
        self._entitlements = entitlements
        self._terms = terms
        self._policies = policies
        self._verification = verification

    async def prepare(self, games: Games) -> list[tuple[Game, Account]]:
        """Create the accounts that the games' dedicated servers log in with.

        They exist before any player could register their names, which every server of the
        game needs to log in.

        Args:
            games: The games the server serves.

        Returns:
            Each game with its dedicated servers' account.
        """
        prepared = []
        for game, nuid in games.server_accounts:
            await self._store.add(nuid, None, "", None, server=True)
            credentials = await self._store.find(nuid)
            if credentials is None or not credentials.account.server:
                logger.error("The dedicated servers' account %s belongs to a player", nuid)
            else:
                prepared.append((game, credentials.account))
        return prepared

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("acct", "NuLogin"): Route(NuLogin, self.login),
            ("acct", "NuAddAccount"): Route(NuAddAccount, self.add_account),
            ("acct", "NuEntitleGame"): Route(NuEntitleGame, self.entitle_game),
            ("acct", "NuGetTos"): Route(NuGetTos, self.terms),
            ("acct", "GetCountryList"): Route(Message, self.country_list),
            **{("acct", txn): _refusal(UNKNOWN_USER) for txn in CONSOLE_LOGINS},
            **{("acct", txn): _refusal(PARAMETERS_ERROR) for txn in NOT_OFFERED},
        }

    async def login(self, connection: FeslConnection, request: NuLogin) -> NuLoginAnswer:
        """Log an account in, a player with its password or token, a dedicated server by name.

        Args:
            connection: The FESL connection the request came on.
            request: The login.

        Raises:
            TransactionError: When the account is unknown, the password or token is wrong, the
                account waits for its address to be confirmed, the player is logged in
                elsewhere and the game's policy refuses a second login, the terms are out of
                date, a player lacks the game's entitlement, or a player and a server account
                meet.
        """
        assert connection.hello is not None and connection.client is not None
        assert connection.game is not None
        policies = await self._policies.of(connection.game)
        token = None
        # The client string decides, not the client type the game declares, so a player cannot
        # skip the game's entitlement by connecting as a dedicated server's client.
        if expected := connection.client.server_account:
            if connection.hello.client_type != "server":
                raise TransactionError(INVALID_PASSWORD)
            account = await self._server_account(expected, request.nuid)
        else:
            account, token = await self._player_account(connection, request, policies)
        key = secrets.token_urlsafe(20) + "."
        connection.session.log_in(account, key, connection, replace=_replaces(policies))
        logger.info("Account %d (%s) logged in", account.id, account.nuid)
        return NuLoginAnswer(
            lkey=key, nuid=account.nuid, user_id=account.id, encrypted_login_info=token
        )

    async def add_account(self, connection: FeslConnection, request: NuAddAccount) -> Message:
        """Register an account from the game's form, keeping only what the service needs.

        Args:
            connection: The FESL connection the request came on.
            request: The form's fields.

        Raises:
            TransactionError: When fields are invalid, the player is too young for the game's
                age check, or the name is taken.
        """
        assert connection.hello is not None and connection.game is not None
        policies = await self._policies.of(connection.game)
        verify = policies.accounts.email_verification
        errors = []
        if (
            not 3 <= len(request.nuid) <= MAXIMUM_NAME
            or not request.nuid.isprintable()
            or (verify and not _ADDRESS.fullmatch(request.nuid))
        ):
            errors.append(FieldError(field_name="email", field_error=INVALID_VALUE))
        if error := password_error("password", request.password):
            errors.append(error)
        if not is_country(request.country):
            errors.append(FieldError(field_name="country", field_error=INVALID_VALUE))
        if error := birth_date_error(
            request.dob_year,
            request.dob_month,
            request.dob_day,
            connection.game.ages(request.country),
            policies.registration,
        ):
            errors.append(error)
        if errors:
            raise TransactionError(PARAMETERS_ERROR, errors)
        password_hash = await self._logins.hash(request.password)
        accepted = request.tos_version if request.tos_version == self._terms.version else None
        token, link = new_link() if verify else (None, None)
        account = await self._store.add(
            request.nuid, password_hash, request.country.upper(), accepted, verification=link
        )
        if account is None:
            raise TransactionError(ALREADY_REGISTERED)
        logger.info("Account %d (%s) registered", account.id, account.nuid)
        if token and link:
            language = language_of(connection.hello.locale)
            self._verification.send(account.nuid, token, link, language)
        return Message()

    async def terms(self, connection: FeslConnection, request: NuGetTos) -> NuGetTosAnswer:
        """Return the terms in the game's language.

        Args:
            connection: The FESL connection the request came on.
            request: The request, whose country the terms do not depend on.
        """
        assert connection.hello is not None
        return NuGetTosAnswer(
            version=self._terms.version, tos=self._terms.text(connection.hello.locale)
        )

    async def country_list(
        self, connection: FeslConnection, request: Message
    ) -> GetCountryListAnswer:
        """Return the countries of the registration form, named in the game's language.

        Args:
            connection: The FESL connection the request came on.
            request: The request, which has no fields.
        """
        assert connection.hello is not None and connection.game is not None
        game = connection.game
        return GetCountryListAnswer(
            country_list=[
                Country(
                    description=name,
                    iso_code=code,
                    registration_age_limit=game.ages(code).registration_age,
                    parental_control_age_limit=game.ages(code).parental_age,
                )
                for code, name in countries(connection.hello.locale)
            ]
        )

    async def entitle_game(self, connection: FeslConnection, request: NuEntitleGame) -> Message:
        """Redeem a product key for an account that could not log in without the game.

        Args:
            connection: The FESL connection the request came on.
            request: The key and the login it is for.

        Raises:
            TransactionError: When the login fails, no enabled key has that code, the key does
                not grant the game, its limits refuse the account, or the account's entitlement
                to the game was taken away.
        """
        assert connection.client is not None
        credentials = await self._authenticate(
            request.nuid, request.password, request.encrypted_info
        )
        account = credentials.account
        # Only a key for the game itself, so the game does not loop between its key prompt
        # and NuLogin's 120, and a key for downloadable content is not used up here.
        game = connection.client.entitlement
        grants = await self._entitlements.grants(request.key)
        if grants is None or (game and game not in {grant.tag for grant in grants}):
            raise TransactionError(KeyRefusal.UNKNOWN)
        redeemed = await self._entitlements.redeem(account.id, request.key)
        if isinstance(redeemed, KeyRefusal):
            raise TransactionError(redeemed)
        if game and not await self._entitlements.entitled(account.id, game):
            raise TransactionError(NOT_ENTITLED)
        logger.info("Account %d redeemed a product key for the game", account.id)
        return Message()

    async def _player_account(
        self, connection: FeslConnection, request: NuLogin, policies: Policies
    ) -> tuple[Account, str | None]:
        assert connection.hello is not None and connection.client is not None
        entitlement = connection.client.entitlement
        credentials = await self._authenticate(
            request.nuid, request.password, request.encrypted_info
        )
        account = credentials.account
        # Only while the policy is on, so turning it off lets waiting accounts in.
        if policies.accounts.email_verification:
            await self._verification.require(account, language_of(connection.hello.locale))
        # Before a token is remembered: an account keeps only its newest tokens, so those of
        # refused logins would push out the one of the session it is logged in on.
        connection.session.admit(account, replace=_replaces(policies))
        if account.terms_version != self._terms.version:
            if request.tos_version != self._terms.version:
                raise TransactionError(TERMS_OUT_OF_DATE)
            await self._store.accept_terms(account.id, self._terms.version)
        if entitlement and not await self._entitlements.entitled(account.id, entitlement):
            raise TransactionError(NOT_ENTITLED)
        # The game's own remembered login asks for no token but keeps whatever the answer
        # carries, nothing included, so a token login always gets its token back.
        if credentials.password_hash is None:
            return account, request.encrypted_info
        if not request.return_encrypted_info:
            return account, None
        return account, await self._logins.remember(account.id, credentials.password_hash)

    async def _authenticate(self, nuid: str, password: str, token: str) -> Credentials:
        if token:
            return Credentials(await self._logins.recall(token), None)
        return await self._logins.check(nuid, password)

    async def _server_account(self, expected: str, nuid: str) -> Account:
        # Every dedicated server of a game logs in with the same account from its game data,
        # so its password proves nothing; servers are told apart and approved in Theater.
        matches = nuid.casefold() == expected.casefold()
        credentials = await self._store.find(nuid) if matches else None
        if credentials is None or not credentials.account.server:
            raise TransactionError(INVALID_PASSWORD)
        return credentials.account


def password_error(field_name: str, password: str) -> FieldError | None:
    """Return what is wrong with a new password from a form, if anything.

    Args:
        field_name: The form's field that holds the password.
        password: The password.
    """
    if len(password) < MINIMUM_PASSWORD:
        return FieldError(field_name=field_name, field_error=INVALID_VALUE)
    if len(password.encode()) > MAXIMUM_PASSWORD:
        return FieldError(field_name=field_name, field_error=TOO_LONG)
    return None


def _replaces(policies: Policies) -> bool:
    return policies.accounts.concurrent_login is ConcurrentLogin.REPLACE


def _refusal(code: int) -> Route[Message]:
    async def refuse(connection: FeslConnection, request: Message) -> None:
        raise TransactionError(code)

    return Route(Message, refuse)
