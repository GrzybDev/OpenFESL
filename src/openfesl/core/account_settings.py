import logging
from typing import Any

from openfesl.core.accounts import Account, AccountStore, password_error
from openfesl.core.entitlements import NOT_LOGGED_IN
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.logins import UNKNOWN_USER, Logins
from openfesl.core.registration import INVALID_VALUE, birth_date_error, is_country
from openfesl.protocol.acct import (
    NuGetAccountAnswer,
    NuGetAccountByNuid,
    NuUpdateAccount,
    NuUpdatePassword,
    NuUpdatePasswordAnswer,
)
from openfesl.protocol.fesl import FieldError
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)


class AccountSettingsService:
    """The acct transactions that show a player's account and change it or its password."""

    def __init__(self, store: AccountStore, logins: Logins) -> None:
        """Serve account settings from a store.

        Args:
            store: Where accounts are kept.
            logins: Checks passwords and hands out remembered logins.
        """
        self._store = store
        self._logins = logins

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("acct", "NuGetAccount"): Route(Message, self.get_account),
            ("acct", "NuGetAccountByNuid"): Route(NuGetAccountByNuid, self.get_account_by_nuid),
            ("acct", "NuUpdateAccount"): Route(NuUpdateAccount, self.update_account),
            ("acct", "NuUpdatePassword"): Route(NuUpdatePassword, self.update_password),
        }

    async def get_account(self, connection: FeslConnection, request: Message) -> NuGetAccountAnswer:
        """Return the details of the session's account.

        Args:
            connection: The FESL connection the request came on.
            request: The request, which has no fields.

        Raises:
            TransactionError: When no player is logged in.
        """
        credentials = await self._store.find(_player(connection).nuid)
        if credentials is None:
            raise TransactionError(UNKNOWN_USER)
        return _details(credentials.account)

    async def get_account_by_nuid(
        self, connection: FeslConnection, request: NuGetAccountByNuid
    ) -> NuGetAccountAnswer:
        """Return the details of the account that a name and password belong to.

        Args:
            connection: The FESL connection the request came on.
            request: The account's name and password.

        Raises:
            TransactionError: When the name or password is wrong.
        """
        return _details((await self._logins.check(request.nuid, request.password)).account)

    async def update_account(self, connection: FeslConnection, request: NuUpdateAccount) -> Message:
        """Change the country of the session's account, the only detail of the form it keeps.

        Args:
            connection: The FESL connection the request came on.
            request: The form, with the account's name and password.

        Raises:
            TransactionError: When no player is logged in, the password is wrong, the form names
                another account or a field is invalid.
        """
        account = _player(connection)
        await self._logins.check(account.nuid, request.password)
        errors = []
        # The login name cannot change: it is what the account logs in with.
        if request.nuid.casefold() != account.nuid.casefold():
            errors.append(FieldError(field_name="email", field_error=INVALID_VALUE))
        if request.country and not is_country(request.country):
            errors.append(FieldError(field_name="country", field_error=INVALID_VALUE))
        born = (request.dob_year, request.dob_month, request.dob_day)
        if any(part is not None for part in born) and (
            error := birth_date_error(*(part or 0 for part in born))
        ):
            errors.append(error)
        if errors:
            raise TransactionError(PARAMETERS_ERROR, errors)
        if request.country:
            await self._store.change_country(account.id, request.country.upper())
        return Message()

    async def update_password(
        self, connection: FeslConnection, request: NuUpdatePassword
    ) -> NuUpdatePasswordAnswer:
        """Change the password of the session's account, forgetting its remembered logins.

        Args:
            connection: The FESL connection the request came on.
            request: The current and the new password.

        Raises:
            TransactionError: When no player is logged in, the current password is wrong or the
                new one is too short or too long.
        """
        account = _player(connection)
        await self._logins.check(account.nuid, request.password)
        if error := password_error("newPassword", request.new_password):
            raise TransactionError(PARAMETERS_ERROR, [error])
        password_hash = await self._logins.hash(request.new_password)
        await self._store.change_password(account.id, password_hash)
        logger.info("Account %d changed its password", account.id)
        token = None
        if request.return_encrypted_info:
            token = await self._logins.remember(account.id, password_hash)
        return NuUpdatePasswordAnswer(encrypted_password_info=token)


def _player(connection: FeslConnection) -> Account:
    account = connection.session.account
    if account is None:
        raise TransactionError(NOT_LOGGED_IN)
    if account.server:
        raise TransactionError(PARAMETERS_ERROR)
    return account


def _details(account: Account) -> NuGetAccountAnswer:
    return NuGetAccountAnswer(nuid=account.nuid, user_id=account.id, country=account.country)
