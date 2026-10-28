from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

from openfesl.core.inventory import Ownership
from openfesl.core.wallets import Wallet


@dataclass(frozen=True, slots=True)
class Grant:
    """What a claim gives at once, all of it or none.

    Attributes:
        game: The game's data-file name.
        account_id: The account.
        key: What the reward is claimed by.
        faction: The faction its part is for; none for the account's own.
        hero: The persona of the hero its items go to; none when it gives a hero nothing.
        items: The items the hero gets, each with its ownership.
        fallback: What the hero gets instead of an item it owns for good.
        funds: The account's wallet the Funds go to, and how many.
        keys: The tag of the account's keys, and how many it gets.
    """

    game: str
    account_id: int
    key: str
    faction: str | None
    hero: int | None
    items: tuple[tuple[int, Ownership], ...]
    fallback: tuple[int, Ownership]
    funds: tuple[Wallet, int] | None
    keys: tuple[str, int] | None


class RewardStore(Protocol):
    """Where claims are recorded and their rewards given."""

    async def claimed(
        self, game: str, account_id: int, keys: Collection[str]
    ) -> set[tuple[str, str | None]]:
        """Return which rewards an account claimed, each by its key and faction.

        Args:
            game: The game's data-file name.
            account_id: The account.
            keys: The keys of the rewards to look at.
        """

    async def grant(self, grant: Grant) -> list[int]:
        """Record a claim and give what it grants, all of it or none.

        Args:
            grant: What the claim gives.

        Returns:
            The items the hero got, an owned one replaced by the fallback.

        Raises:
            RewardRefused: When the account claimed the reward already, also at the same time.
        """
