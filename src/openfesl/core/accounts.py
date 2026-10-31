import logging
import re
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from openfesl.core.admins import Admin
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game, Games
from openfesl.core.identities import Identity
from openfesl.core.keys import KeyRefusal, KeyStore
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
ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

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
        state_changed: When its state last changed, if it ever did.
        verification_expires_at: When the link that confirms its e-mail address expires, while
            it waits for that; None once confirmed or when it never had to be.
        display_name: The public name that the site shows for it, once it has one.
        made_in: The id of the game whose client registered it; None for the site's own
            registrations.
        session_epoch: The number its web sessions carry; a session with another one ended.
    """

    id: int
    nuid: str
    country: str
    terms_version: str | None
    server: bool
    state: AccountState
    state_until: datetime | None
    state_changed: datetime | None
    verification_expires_at: datetime | None
    display_name: str | None
    made_in: str | None
    session_epoch: int

    def login_error(self) -> int | None:
        """Return the NuLogin error that the account's state refuses its logins with, if any."""
        if self.state_until is not None and self.state_until <= datetime.now(UTC):
            return None
        return STATE_ERRORS.get(self.state)

    @property
    def public(self) -> bool:
        """Whether others see the account: in use, as logins also take one whose state ended."""
        # And named, as the player search finds it.
        return self.login_error() is None and bool(self.display_name)


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """A player's account with what the admin panel shows about it.

    Attributes:
        account: The account.
        created: When it was registered.
        last_login: When it last logged in, if it ever did.
    """

    account: Account
    created: datetime
    last_login: datetime | None


@dataclass(frozen=True, slots=True)
class RememberedLogin:
    """A login that the game remembers for an account; only when it was handed out is known.

    Attributes:
        id: The digest of its token in hex, which names it in the admin panel.
        created: When it was handed out.
    """

    id: str
    created: datetime


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

    async def account(self, account_id: int) -> Account | None:
        """Return an account by its number.

        Args:
            account_id: The account's number.
        """

    async def record(self, account_id: int) -> AccountRecord | None:
        """Return a player's account by its number, with when it was made.

        Args:
            account_id: The account's number.
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
        made_in: str | None = None,
        display_name: str | None = None,
        identity: Identity | None = None,
        recruited_by: tuple[str, int] | None = None,
    ) -> Account | None:
        """Create an account, or return None when its name, display name or identity is taken.

        Args:
            nuid: The login name.
            password_hash: The password's hash; None for a server account.
            country: The ISO code chosen on registration.
            terms_version: The version of the terms accepted on registration, if current.
            server: Whether the account belongs to a game's dedicated servers.
            verification: The link that must confirm its e-mail address before it logs in.
            made_in: The id of the game whose client registers it; None for the site.
            display_name: Its public display name, which the site's registration gives it.
            identity: An identity at a sign-in service that becomes a way in with it; nothing is
                made while the identity opens another account.
            recruited_by: The game and the account whose personal link brought the registration,
                which makes the new account its recruit while that account still exists.
        """

    async def display_name_taken(self, name: str) -> bool:
        """Return whether an account uses a display name, compared regardless of case.

        Args:
            name: The display name.
        """

    async def name_account(self, account_id: int, name: str) -> bool:
        """Give an account a display name, in place of the one it had, if any.

        Args:
            account_id: The account.
            name: The display name.

        Returns:
            Whether the account has it now, which it has not when another account uses it,
            ignoring case.
        """

    async def accept_terms(self, account_id: int, version: str) -> None:
        """Record that an account accepted a version of the terms.

        Args:
            account_id: The account.
            version: The version it accepted.
        """

    async def remember(self, account_id: int, token_hash: bytes, password_hash: str | None) -> bool:
        """Keep a login token so the game can log in without the password later.

        Args:
            account_id: The account.
            token_hash: The SHA-256 digest of the token handed to the game.
            password_hash: The hash the login's password was checked against; None for an
                account without a password.

        Returns:
            Whether the token was kept, which it is not once the password changed.
        """

    async def record_login(self, account_id: int) -> None:
        """Note that a player's account logged in now.

        Args:
            account_id: The account.
        """

    async def change_password(self, account_id: int, password_hash: str) -> None:
        """Replace an account's password, ending its web sessions and remembered logins.

        Args:
            account_id: The account.
            password_hash: The new password's hash.
        """

    async def request_deletion(self, account_id: int) -> datetime:
        """Deactivate an account at its player's request, ending its web sessions, and record it.

        Args:
            account_id: The account.

        Returns:
            When it was deactivated.
        """

    async def end_sessions(self, account_id: int) -> None:
        """End an account's web sessions and the logins the games remember for it.

        Args:
            account_id: The account.
        """

    async def remembered(self, account_id: int) -> list[RememberedLogin]:
        """Return the logins remembered for an account that still log it in, newest first.

        Args:
            account_id: The account.
        """

    async def remove_password(self, account_id: int, services: Collection[str]) -> bool:
        """Remove an account's password, ending its web sessions and remembered logins.

        Args:
            account_id: The account.
            services: The ids of the configured sign-in services, whose identities alone sign in.

        Returns:
            Whether it was removed, which it is not while no identity at a configured sign-in
            service is linked to the account, as the password is then its last way in.
        """

    async def change_country(self, account_id: int, country: str) -> None:
        """Replace the country an account chose.

        Args:
            account_id: The account.
            country: The ISO code of the new country.
        """

    async def set_state(
        self, account_id: int, state: AccountState, until: datetime | None, admin: Admin | None
    ) -> None:
        """Change whether an account may log in, ending its web sessions, and record the change.

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
        keys: KeyStore,
        terms: Terms,
        policies: GamePolicies,
        verification: EmailVerification,
        games: Games,
    ) -> None:
        """Serve accounts from a store.

        Args:
            store: Where accounts are kept.
            logins: Checks passwords and remembered logins.
            entitlements: Where the entitlements that let players in are kept.
            keys: Where the product keys that grant them are kept.
            terms: The service's terms.
            policies: The site's and each game's policies.
            verification: Confirms the e-mail addresses of new accounts.
            games: The games, which the accounts they registered name.
        """
        self._store = store
        self._logins = logins
        self._entitlements = entitlements
        self._keys = keys
        self._terms = terms
        self._policies = policies
        self._verification = verification
        self._games = games

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
        # The client the Hello stands for decides: a dedicated server's client logs in nothing
        # but its server account, so posing as a server cannot skip the game's entitlement.
        if expected := connection.client.server_account:
            if connection.hello.client_type != "server":
                raise TransactionError(INVALID_PASSWORD)
            account = await self._server_account(expected, request.nuid)
        else:
            account, token = await self._player_account(connection, request, policies)
        key = secrets.token_urlsafe(20) + "."
        connection.session.log_in(account, key, connection, replace=_replaces(policies))
        if not account.server:
            await self._store.record_login(account.id)
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
            or (verify and not ADDRESS.fullmatch(request.nuid))
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
            request.nuid,
            password_hash,
            request.country.upper(),
            accepted,
            verification=link,
            made_in=connection.game.id,
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
        needed = connection.client.entitlement
        grants = await self._keys.grants(request.key)
        if grants is None or (needed and not any(grant.provides(needed) for grant in grants)):
            raise TransactionError(KeyRefusal.UNKNOWN)
        redeemed = await self._keys.redeem(account.id, request.key)
        if isinstance(redeemed, KeyRefusal):
            raise TransactionError(redeemed)
        if needed and not await self._entitlements.entitled(account.id, needed.tag, needed.product):
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
        # The policy the account was registered under decides, in whichever game it logs in.
        made_in = self._games.by_id(account.made_in or "")
        if account.verification_expires_at and await self._policies.confirms(made_in):
            await self._verification.require(account, language_of(connection.hello.locale))
        # Before a token is remembered: an account keeps only its newest tokens, so those of
        # refused logins would push out the one of the session it is logged in on.
        connection.session.admit(account, replace=_replaces(policies))
        if account.terms_version != self._terms.version:
            if request.tos_version != self._terms.version:
                raise TransactionError(TERMS_OUT_OF_DATE)
            await self._store.accept_terms(account.id, self._terms.version)
        if entitlement and not await self._entitlements.entitled(
            account.id, entitlement.tag, entitlement.product
        ):
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
