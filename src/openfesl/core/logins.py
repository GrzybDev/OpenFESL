import asyncio
import hashlib
import secrets
from typing import TYPE_CHECKING

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from openfesl.core.fesl import TransactionError

# For annotations only, since the account service imports this module.
if TYPE_CHECKING:
    from openfesl.core.accounts import Account, AccountStore, Credentials

UNKNOWN_USER = 101
INVALID_PASSWORD = 122

# Argon2 is slow on purpose; this many hashes at once keep logins from starving the server.
CONCURRENT_HASHES = 4


class Logins:
    """Hashes passwords and checks the logins of players by password or remembered token."""

    def __init__(self, store: AccountStore) -> None:
        """Check logins against a store.

        Args:
            store: Where accounts and remembered logins are kept.
        """
        self._store = store
        self._hasher = PasswordHash((Argon2Hasher(),))
        self._hashing = asyncio.Semaphore(CONCURRENT_HASHES)

    async def hash(self, password: str) -> str:
        """Return the hash a password is kept as.

        Args:
            password: The password.
        """
        async with self._hashing:
            return await asyncio.to_thread(self._hasher.hash, password)

    async def check(self, nuid: str, password: str) -> Credentials:
        """Return the player account that a name and password belong to, with the hash checked.

        Args:
            nuid: The login name, matched regardless of case.
            password: The password.

        Raises:
            TransactionError: When no account has the name, the password is wrong or the account
                belongs to dedicated servers.
        """
        credentials = await self._store.find(nuid)
        if credentials is None:
            raise TransactionError(UNKNOWN_USER)
        if not credentials.password_hash or credentials.account.server:
            raise TransactionError(INVALID_PASSWORD)
        async with self._hashing:
            valid = await asyncio.to_thread(
                self._hasher.verify, password, credentials.password_hash
            )
        if not valid:
            raise TransactionError(INVALID_PASSWORD)
        return credentials

    async def recall(self, token: str) -> Account:
        """Return the player account that a remembered login belongs to.

        Args:
            token: The token the game kept from an earlier login.

        Raises:
            TransactionError: When the token is unknown or too old.
        """
        account = await self._store.find_remembered(_digest(token))
        if account is None or account.server:
            raise TransactionError(INVALID_PASSWORD)
        return account

    async def remember(self, account_id: int, password_hash: str) -> str:
        """Hand out a token that logs an account in later without its password.

        Args:
            account_id: The account.
            password_hash: The hash the login's password was checked against.

        Raises:
            TransactionError: When the password changed since it was checked, so that a login
                with the old password keeps no way back in.
        """
        token = secrets.token_urlsafe(32)
        if not await self._store.remember(account_id, _digest(token), password_hash):
            raise TransactionError(INVALID_PASSWORD)
        return token


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).digest()
