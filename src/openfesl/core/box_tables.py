from fractions import Fraction
from typing import Protocol

from openfesl.core.drops import Box, odds


class BoxStore(Protocol):
    """Where the boxes of the games' stores and their drop tables are kept."""

    async def boxes(self, game: str) -> list[Box]:
        """Return every box of a game's store with its drops, boxes before chests.

        Args:
            game: The game's data-file name.
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
