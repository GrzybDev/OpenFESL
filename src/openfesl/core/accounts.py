import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Games
from openfesl.core.registration import Terms, countries, is_country
from openfesl.protocol.acct import (
    Country,
    GetCountryListAnswer,
    NuAddAccount,
    NuGetTos,
    NuGetTosAnswer,
    NuLogin,
    NuLoginAnswer,
)
from openfesl.protocol.fesl import FieldError
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)

UNKNOWN_USER = 101
INVALID_PASSWORD = 122
ALREADY_REGISTERED = 160
TERMS_OUT_OF_DATE = 260

TOO_LONG = 2
INVALID_VALUE = 6
TOO_YOUNG = 16

MINIMUM_AGE = 13
MINIMUM_PASSWORD = 8
# The SDK keeps 31 bytes of a password and logs in again after registration with what it kept.
MAXIMUM_PASSWORD = 31
MAXIMUM_NAME = 63
# Argon2 is slow on purpose; this many hashes at once keep logins from starving the server.
CONCURRENT_HASHES = 4


@dataclass(frozen=True, slots=True)
class Account:
    """An account as the services see it.

    Attributes:
        id: The account's number, which the game knows as its user id.
        nuid: The name the account logs in with.
        country: The ISO code chosen on registration.
        terms_version: The version of the terms the account accepted last.
        server: Whether the account belongs to a game's dedicated servers.
    """

    id: int
    nuid: str
    country: str
    terms_version: str | None
    server: bool


@dataclass(frozen=True, slots=True)
class Credentials:
    """An account with the hash its password is checked against; server accounts have none."""

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
    ) -> Account | None:
        """Create an account, or return None when the name is taken.

        Args:
            nuid: The login name.
            password_hash: The password's hash; None for a server account.
            country: The ISO code chosen on registration.
            terms_version: The version of the terms accepted on registration, if current.
            server: Whether the account belongs to a game's dedicated servers.
        """

    async def accept_terms(self, account_id: int, version: str) -> None:
        """Record that an account accepted a version of the terms.

        Args:
            account_id: The account.
            version: The version it accepted.
        """

    async def remember(self, account_id: int, token_hash: bytes) -> None:
        """Keep a login token so the game can log in without the password later.

        Args:
            account_id: The account.
            token_hash: The SHA-256 digest of the token handed to the game.
        """


class AccountService:
    """The acct transactions that register accounts and log them in."""

    def __init__(self, store: AccountStore, terms: Terms) -> None:
        """Serve accounts from a store.

        Args:
            store: Where accounts are kept.
            terms: The service's terms.
        """
        self._store = store
        self._terms = terms
        self._hasher = PasswordHash((Argon2Hasher(),))
        self._hashing = asyncio.Semaphore(CONCURRENT_HASHES)

    async def prepare(self, games: Games) -> None:
        """Create the accounts that the games' dedicated servers log in with.

        They exist before any player could register their names, which every server of the
        game needs to log in.

        Args:
            games: The games the server serves.
        """
        for nuid in games.server_accounts:
            await self._store.add(nuid, None, "", None, server=True)
            credentials = await self._store.find(nuid)
            if credentials is None or not credentials.account.server:
                logger.error("The dedicated servers' account %s belongs to a player", nuid)

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("acct", "NuLogin"): Route(NuLogin, self.login),
            ("acct", "NuAddAccount"): Route(NuAddAccount, self.add_account),
            ("acct", "NuGetTos"): Route(NuGetTos, self.terms),
            ("acct", "GetCountryList"): Route(Message, self.country_list),
        }

    async def login(self, connection: FeslConnection, request: NuLogin) -> NuLoginAnswer:
        """Log an account in, a player with its password or token, a dedicated server by name.

        Args:
            connection: The FESL connection the request came on.
            request: The login.

        Raises:
            TransactionError: When the account is unknown, the password or token is wrong, the
                terms are out of date, or a player and a server account meet.
        """
        assert connection.hello is not None and connection.client is not None
        token = None
        # The client string decides, not the client type the game declares, so a player cannot
        # skip the game's entitlement by connecting as a dedicated server's client.
        if expected := connection.client.server_account:
            if connection.hello.client_type != "server":
                raise TransactionError(INVALID_PASSWORD)
            account = await self._server_account(expected, request.nuid)
        else:
            account, token = await self._player_account(request)
        key = secrets.token_urlsafe(20) + "."
        connection.session.log_in(account, key, connection)
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
            TransactionError: When fields are invalid or the name is taken.
        """
        errors = []
        if not 3 <= len(request.nuid) <= MAXIMUM_NAME or not request.nuid.isprintable():
            errors.append(FieldError(field_name="email", field_error=INVALID_VALUE))
        if error := password_error("password", request.password):
            errors.append(error)
        if not is_country(request.country):
            errors.append(FieldError(field_name="country", field_error=INVALID_VALUE))
        try:
            born = date(request.dob_year, request.dob_month, request.dob_day)
        except ValueError, OverflowError:
            errors.append(FieldError(field_name="dob", field_error=INVALID_VALUE))
        else:
            if _age(born) < MINIMUM_AGE:
                errors.append(FieldError(field_name="dob", field_error=TOO_YOUNG))
        if errors:
            raise TransactionError(PARAMETERS_ERROR, errors)
        async with self._hashing:
            password_hash = await asyncio.to_thread(self._hasher.hash, request.password)
        accepted = request.tos_version if request.tos_version == self._terms.version else None
        account = await self._store.add(
            request.nuid, password_hash, request.country.upper(), accepted
        )
        if account is None:
            raise TransactionError(ALREADY_REGISTERED)
        logger.info("Account %d (%s) registered", account.id, account.nuid)
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
        assert connection.hello is not None
        return GetCountryListAnswer(
            country_list=[
                Country(description=name, iso_code=code)
                for code, name in countries(connection.hello.locale)
            ]
        )

    async def _player_account(self, request: NuLogin) -> tuple[Account, str | None]:
        if request.encrypted_info:
            account = await self._store.find_remembered(_digest(request.encrypted_info))
            if account is None:
                raise TransactionError(INVALID_PASSWORD)
            # The game's own remembered login asks for no token but keeps whatever the answer
            # carries, nothing included, so a token login always gets its token back.
            token = request.encrypted_info
        else:
            credentials = await self._store.find(request.nuid)
            if credentials is None:
                raise TransactionError(UNKNOWN_USER)
            if not credentials.password_hash or not await self._verify(
                request.password, credentials.password_hash
            ):
                raise TransactionError(INVALID_PASSWORD)
            account, token = credentials.account, None
        if account.server:
            raise TransactionError(INVALID_PASSWORD)
        if account.terms_version != self._terms.version:
            if request.tos_version != self._terms.version:
                raise TransactionError(TERMS_OUT_OF_DATE)
            await self._store.accept_terms(account.id, self._terms.version)
        if request.return_encrypted_info and token is None:
            token = secrets.token_urlsafe(32)
            await self._store.remember(account.id, _digest(token))
        return account, token

    async def _server_account(self, expected: str, nuid: str) -> Account:
        # Every dedicated server of a game logs in with the same account from its game data,
        # so its password proves nothing; servers are told apart and approved in Theater.
        matches = nuid.casefold() == expected.casefold()
        credentials = await self._store.find(nuid) if matches else None
        if credentials is None or not credentials.account.server:
            raise TransactionError(INVALID_PASSWORD)
        return credentials.account

    async def _verify(self, password: str, password_hash: str) -> bool:
        async with self._hashing:
            return await asyncio.to_thread(self._hasher.verify, password, password_hash)


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


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).digest()


def _age(born: date) -> int:
    today = datetime.now(UTC).date()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
