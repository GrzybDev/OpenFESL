from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openfesl.core.inventory import Ownership
from openfesl.core.stats import Bucket
from openfesl.core.wallets import Wallet


@dataclass(frozen=True, slots=True)
class Claim:
    """A reward an account claimed, as it was given.

    Attributes:
        key: What it was claimed by.
        faction: The faction of its part; none for the account's own.
        hero: The persona of the hero its items went to; none when they went to no hero, or the
            hero is gone.
        items: The items the hero got, each with its ownership.
        funds: The Funds the account got.
        keys: The treasure chest keys the account got.
        at: When it was claimed.
    """

    key: str
    faction: str | None
    hero: int | None
    items: tuple[tuple[int, Ownership], ...]
    funds: int
    keys: int
    at: datetime


@dataclass(frozen=True, slots=True)
class Claimed:
    """A reward an account claimed, with what names it.

    Attributes:
        claim: The claim.
        name: The id of the reward's name in the game's texts or the server's own; the key
            itself for a reward that the rules no longer give.
        goal: The goal of a milestone, which its name states; none for anything else.
    """

    claim: Claim
    name: str
    goal: int | None


@dataclass(frozen=True, slots=True)
class Given:
    """What a claim gave a hero.

    Attributes:
        items: The items the hero got, each with its ownership, one it owned for good
            replaced by the fallback.
        rolled: Whether they are the one item that the gift's box gave.
    """

    items: list[tuple[int, Ownership]]
    rolled: bool


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
        at: When it is claimed.
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
    at: datetime


class RewardStore(Protocol):
    """Where claims are recorded and their rewards given."""

    async def claimed(self, game: str, account_id: int, keys: Collection[str]) -> list[Claim]:
        """Return the claims an account made of rewards, whenever it made them.

        Args:
            game: The game's data-file name.
            account_id: The account.
            keys: The keys of the rewards to look at.
        """

    async def baselines(
        self,
        game: str,
        partition: str,
        key: str,
        stat: str,
        bucket: Bucket,
        personas: Collection[int],
    ) -> dict[int, int]:
        """Return the values a stat of heroes had when an event began, taking them on first use.

        The first use takes every hero's value at once, so that a hero without the stat then
        counts from 0; later uses read what it took.

        Args:
            game: The game's data-file name.
            partition: The game's partition, which holds the stats.
            key: The event's key, of its year.
            stat: The stat.
            bucket: The period of the stat's values.
            personas: The heroes whose values to return.

        Returns:
            The values the heroes had, by persona; a hero without one had none.
        """

    async def grant(self, grant: Grant) -> list[tuple[int, Ownership]]:
        """Record a claim and give what it grants, all of it or none.

        Args:
            grant: What the claim gives.

        Returns:
            The items the hero got, each with its ownership, an owned one replaced by the
            fallback.

        Raises:
            RewardRefused: When the account claimed the reward already, also at the same time.
        """

    async def claims(self, game: str, account_id: int, since: datetime) -> list[Claim]:
        """Return the rewards an account claimed since a moment, the latest first.

        Args:
            game: The game's data-file name.
            account_id: The account.
            since: The moment.
        """
