from dataclasses import dataclass
from datetime import datetime

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.problems import Problem
from openfesl.core.registration import is_country


@dataclass(frozen=True, slots=True)
class AccountOverview:
    """An account with what its player sees of it on the site.

    Attributes:
        account: The account.
        created: When it was made.
    """

    account: Account
    created: datetime


class AccountManagement:
    """What a signed-in player sees of their account on the site and changes about it."""

    def __init__(self, accounts: AccountStore) -> None:
        """Manage the accounts in a store.

        Args:
            accounts: Where the accounts are kept.
        """
        self._accounts = accounts

    async def overview(self, account_id: int) -> AccountOverview | None:
        """Return what the player of an account sees of it; None for an unknown account.

        Args:
            account_id: The account.
        """
        record = await self._accounts.record(account_id)
        return AccountOverview(record.account, record.created) if record else None

    async def change_country(self, account: Account, country: str) -> Problem | None:
        """Change the country of an account, which games receive with its details.

        The birth date is not kept, so the new country's age limit is not checked again.

        Args:
            account: The account.
            country: The ISO code of the new country.

        Returns:
            What is wrong with the country, if anything; nothing is saved then.
        """
        if not is_country(country):
            return Problem("country", "missing")
        await self._accounts.change_country(account.id, country.upper())
        return None
