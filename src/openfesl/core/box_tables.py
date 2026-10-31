from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import Any, Protocol

from openfesl.core.admins import Admin
from openfesl.core.drops import Box, odds
from openfesl.core.inventory import Ownership


class TableRefusal(StrEnum):
    """Why a box's drop table is not saved as the admin changed it.

    Weights that do not add up to 100, a tier with a weight and no entry, an entry worth less
    than the box's price while it promises its worth, an entry without a price in the box's
    currency, or the promise switched off on a box whose own text makes it.
    """

    WEIGHTS = "weights"
    EMPTY = "empty"
    BELOW = "below"
    NO_PRICE = "no-price"
    PROMISED = "promised"


class TableRefused(Exception):
    """Raised for a drop table that is not saved."""

    def __init__(
        self, refusal: TableRefusal, entry: TableEntry | None = None, tier: int | None = None
    ) -> None:
        """Say why, and of which entry or tier.

        Args:
            refusal: Why.
            entry: The entry it is about, if one.
            tier: The tier it is about, if one.
        """
        super().__init__(refusal.value)
        self.refusal = refusal
        self.entry = entry
        self.tier = tier


@dataclass(frozen=True, slots=True)
class TableEntry:
    """An entry of a box's drop table as the admin saves it.

    Attributes:
        tier: The tier it is in, from 0.
        item_id: The item it gives.
        ownership: How the hero owns the item.
        price: What it is worth in the box's currency; none when nothing prices it so.
        product: The number of the store's product it gives; none for one to add, unpublished.
    """

    tier: int
    item_id: int
    ownership: Ownership
    price: int | None
    product: int | None


class BoxStore(Protocol):
    """Where the boxes of the games' stores and their drop tables are kept."""

    async def boxes(self, game: str) -> list[Box]:
        """Return every box of a game's store with its drops, boxes before chests.

        Args:
            game: The game's data-file name.
        """

    async def save_table(
        self,
        box: Box,
        weights: Sequence[int],
        entries: Sequence[TableEntry],
        worth_rule: bool,
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Replace a box's drop table and record the change among the admin's actions.

        Args:
            box: The box, as it was.
            weights: The weight of each tier, by its number.
            entries: Its entries; those without a product add one, unpublished, at their price.
            worth_rule: Whether it promises that every entry is worth its price.
            details: What the admin's log keeps of the change.
            admin: The admin who saved it.
        """

    async def switch(self, game: str, box: Box, enabled: bool, admin: Admin) -> None:
        """Put a box on sale or take it off, or let keys open a chest or not, and record it.

        Args:
            game: The game's data-file name.
            box: The box or chest.
            enabled: Whether it is sold, or for a chest whether keys open it.
            admin: The admin who switched it.
        """


def expected(box: Box) -> Fraction | None:
    """Return what a box gives on average against its price, as a share of it.

    Each product counts by its chance in the box's rolls, as a hero who owns none of them gets
    it; a product priced in another currency has no value in it. None for a box without a price
    or that gives nothing.

    Args:
        box: The box.
    """
    chances = odds(box, lambda product: True)
    if not chances or not box.value:
        return None
    value = sum(
        (
            chance * product.price
            for product, chance in chances.items()
            if product.currency == box.currency
        ),
        Fraction(0),
    )
    return value / box.value


def check_table(
    box: Box,
    weights: Sequence[int],
    entries: Sequence[TableEntry],
    worth_rule: bool,
    promised: bool,
) -> None:
    """Check a drop table the admin changed against the rules every table keeps.

    Args:
        box: The box, as it was.
        weights: The weight of each tier, by its number; 0 takes an empty tier out.
        entries: Its entries.
        worth_rule: Whether it promises that every entry is worth its price.
        promised: Whether the box's own text promises that worth.

    Raises:
        TableRefused: When the table breaks one of the rules the refusals name.
    """
    if box.worth_rule and not worth_rule and promised:
        raise TableRefused(TableRefusal.PROMISED)
    for entry in entries:
        if entry.price is None:
            raise TableRefused(TableRefusal.NO_PRICE, entry)
        if worth_rule and entry.price < box.value:
            raise TableRefused(TableRefusal.BELOW, entry)
    filled = {entry.tier for entry in entries}
    for tier, weight in enumerate(weights):
        if weight and tier not in filled:
            raise TableRefused(TableRefusal.EMPTY, tier=tier)
        if tier in filled and not weight:
            raise TableRefused(TableRefusal.WEIGHTS)
    if sum(weights) != 100:
        raise TableRefused(TableRefusal.WEIGHTS)
