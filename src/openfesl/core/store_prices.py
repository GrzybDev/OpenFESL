from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.games import Game
from openfesl.core.hero_items import granted
from openfesl.core.store import Product
from openfesl.core.store_rules import StoreRules


class Unsold(StrEnum):
    """Why a game's store never sells an item, whatever its price points say.

    Withheld by the game's data, held by every hero it fits, in a starting kit that the game
    grants itself, part of a hero's look from its creation, left out by the game's data, or
    priced by no rule.
    """

    WITHHELD = "withheld"
    BASE = "base"
    STARTING = "starting"
    LOOK = "look"
    EXCLUDED = "excluded"
    UNPRICED = "unpriced"


class PriceState(StrEnum):
    """How a game's store sells an item.

    On sale at its rule's prices, not sold, on sale at prices of its own, or at the fixed prices
    of the heroes' points, which the game's menu takes.
    """

    SALE = "sale"
    OFF = "off"
    OWN = "own"
    FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class PriceLine:
    """One price point of an item: a product of the store beside what the item's rule makes it.

    Attributes:
        product: The product.
        rule: What the rule of the item's kind prices it at; none where no rule prices it so.
    """

    product: Product
    rule: int | None

    @property
    def own(self) -> bool:
        """Whether the price is the item's own rather than its rule's."""
        return self.product.price != self.rule


@dataclass(frozen=True, slots=True)
class ItemPrices:
    """An item of a game's catalogue with the price points its store keeps of it.

    Attributes:
        item: The item; an ability's first level for all of its levels.
        lines: Its price points, of each of an ability's levels in turn.
        unsold: Why the store never sells it, if it never does.
        fixed: Whether it is priced in the heroes' points alone, which no admin changes.
    """

    item: Item
    lines: tuple[PriceLine, ...]
    unsold: Unsold | None
    fixed: bool

    @property
    def state(self) -> PriceState:
        """How the store sells it."""
        if self.fixed:
            return PriceState.FIXED
        if self.unsold or not any(line.product.published for line in self.lines):
            return PriceState.OFF
        return PriceState.OWN if any(line.own for line in self.lines) else PriceState.SALE


class PriceStore(Protocol):
    """Where the price points of the games' stores are kept."""

    async def every(self, game: str) -> list[Product]:
        """Return every product of a game's store for heroes, published or not, in order.

        Args:
            game: The game's data-file name.
        """


def item_prices(
    game: Game, rules: StoreRules, catalogue: Catalogue, products: Iterable[Product]
) -> list[ItemPrices]:
    """Return the items of the kinds a game's store prices, each with its price points.

    Items without a name, leftovers of the game's making, are left out, as are an ability's
    later levels, which its first one stands for.

    Args:
        game: The game.
        rules: How its store prices items, with the instance's settings over its data.
        catalogue: Its imported catalogue.
        products: The products its store sells heroes, published or not.
    """
    held: dict[int, list[Product]] = {}
    for product in products:
        held.setdefault(product.item_id, []).append(product)
    kinds = {rule.kind for rule in rules.prices}
    heroes = game.heroes
    starting = (
        {
            number
            for faction in heroes.factions
            for hero_class in heroes.classes
            for number in granted(heroes, faction, hero_class)
        }
        if heroes
        else set()
    )
    points = heroes.points if heroes else None
    found = []
    for item in sorted(catalogue.contents.items.values(), key=lambda item: item.id):
        if item.kind not in kinds or not catalogue.name(item.id):
            continue
        if item.levels and item.id != item.levels[0]:
            continue
        priced = rules.rules(item)
        lines = tuple(
            PriceLine(product, rules.price_of(level, product.ownership, product.currency))
            for number in item.levels or (item.id,)
            if (level := catalogue.item(number))
            for product in held.get(number, [])
        )
        fixed = bool(priced) and all(rule.currency == points for rule in priced)
        found.append(ItemPrices(item, lines, _unsold(rules, item, starting, bool(priced)), fixed))
    return found


def _unsold(rules: StoreRules, item: Item, starting: set[int], priced: bool) -> Unsold | None:
    if item.withheld:
        return Unsold.WITHHELD
    if item.base:
        return Unsold.BASE
    if item.id in starting:
        return Unsold.STARTING
    if item.slot in rules.excluded.slots:
        return Unsold.LOOK
    if item.id in rules.excluded.items:
        return Unsold.EXCLUDED
    return None if priced else Unsold.UNPRICED
