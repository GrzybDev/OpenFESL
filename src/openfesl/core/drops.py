from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from random import Random, SystemRandom
from typing import TYPE_CHECKING

from openfesl.core.catalogues import Catalogues
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game
from openfesl.core.hero_items import fits, granted
from openfesl.core.heroes import Hero
from openfesl.core.inventory import PERMANENT, gains
from openfesl.core.personas import Persona

# For annotations only, since the store's module opens boxes.
if TYPE_CHECKING:
    from openfesl.core.store import Product


@dataclass(frozen=True, slots=True)
class Drop:
    """One of the products a box may give.

    Attributes:
        tier: The tier it is in; a tier's drops are equally likely.
        weight: How likely its tier is against the box's others.
        product: The product, which need not be sold alone.
        rare: Whether its tier is marked rare.
    """

    tier: int
    weight: int
    product: Product
    rare: bool = False


@dataclass(frozen=True, slots=True)
class Box:
    """Products of which a box, such as a supply drop or a treasure chest, gives one at random.

    Attributes:
        id: Its number.
        key: The name its game's data knows it by.
        kind: box, sold as a hot deal, chest, opened with a key, or claw, the Claw's prizes.
        value: What every product it gives is worth at least, in its currency.
        worth_rule: Whether it promises that worth.
        faction: The faction whose heroes may open it; any when none.
        hero_class: The class whose heroes may open it; any when none.
        currency: The currency of its value.
        drops: The products it may give.
        fallback: The product it gives when a hero may get none of the others.
        enabled: Whether it is sold, or for a chest whether keys open it.
    """

    id: int
    key: str
    kind: str
    value: int
    worth_rule: bool
    faction: str | None
    hero_class: str | None
    currency: str
    drops: tuple[Drop, ...]
    fallback: Product | None
    enabled: bool = True


class Drops:
    """Opens the boxes of the games' stores for heroes, by chance among what each may get."""

    def __init__(
        self, catalogues: Catalogues, entitlements: EntitlementStore, random: Random | None = None
    ) -> None:
        """Open boxes by what the catalogues say fits a hero and what the hero holds.

        Args:
            catalogues: The games' imported catalogues, which tell what fits a hero.
            entitlements: Where the items the heroes hold are kept.
            random: The source of chance; the system's own when none.
        """
        self._catalogues = catalogues
        self._entitlements = entitlements
        self._random = random or SystemRandom()

    async def roll(self, game: Game, box: Box, persona: Persona, hero: Hero) -> Product | None:
        """Return what a box gives a hero: one of the drops it may get, else the fallback.

        The hero gets no item it owns for good, those the game grants it itself among them,
        nor one that does not fit its faction, class and level.

        Args:
            game: The hero's game.
            box: The box.
            persona: The hero's persona.
            hero: The hero, with its faction, class and level.

        Returns:
            The product, or None when the hero may get no drop and the box has no fallback.
        """
        return pick(box, await self._may_get(game, persona, hero), self._random)

    async def chances(
        self, game: Game, box: Box, persona: Persona, hero: Hero
    ) -> tuple[dict[Product, Fraction], list[Product]]:
        """Return what a box gives a hero with the chance of each, as roll gives them.

        Args:
            game: The hero's game.
            box: The box.
            persona: The hero's persona.
            hero: The hero, with its faction, class and level.

        Returns:
            The chances, and the drops that fit the hero but are left out as it owns them.
        """
        may_get = await self._may_get(game, persona, hero)
        fits_hero = self._fitting(game, hero.faction, hero.hero_class, hero.level, set())
        owned = [d.product for d in box.drops if fits_hero(d.product) and not may_get(d.product)]
        return odds(box, may_get), owned

    def odds(self, game: Game, box: Box, faction: str, hero_class: str) -> dict[Product, Fraction]:
        """Return what a box gives a hero of a faction and class, with the chance of each.

        The chances are those of a hero at the highest level that owns none of the drops.

        Args:
            game: The game.
            box: The box.
            faction: The hero's faction.
            hero_class: The hero's class.
        """
        level = game.heroes.highest_level if game.heroes else 0
        return odds(box, self._fitting(game, faction, hero_class, level, set()))

    async def _may_get(self, game: Game, persona: Persona, hero: Hero) -> Callable[[Product], bool]:
        # Whether the hero may get a product, by what fits it and what it owns for good.
        kept = await self._entitlements.of_persona(persona.id)
        owned = {int(entry.tag.partition("-")[0]) for entry in kept if _owned(entry.tag)}
        return self._fitting(game, hero.faction, hero.hero_class, hero.level, owned)

    def _fitting(
        self, game: Game, faction: str | None, hero_class: str | None, level: int, owned: set[int]
    ) -> Callable[[Product], bool]:
        # Whether a hero may get a product: it fits the hero, and the hero does not own it for
        # good, nor holds it as the game grants it itself.
        contents = self._catalogues.contents(game)
        owned = owned | {item.id for item in contents.items.values() if item.base}
        owned |= granted(game.heroes, faction, hero_class)

        def may_get(product: Product) -> bool:
            item = contents.items.get(product.item_id)
            return (
                item is not None
                and fits(item, {item.kind}, faction, hero_class, level)
                and gains(product.item_id, product.ownership, owned)
            )

        return may_get


def pick(box: Box, may_get: Callable[[Product], bool], random: Random) -> Product | None:
    """Return the product a box gives a hero: a tier by its weight, then one of its drops.

    Only the drops the hero may get take part, so an item it owns for good is never given
    twice, and with none of them left the box gives its fallback. A box that promises its worth
    skips the drops worth less than its price, and a tier of only such drops gives the fallback,
    so that the other tiers are no likelier than the box's weights make them.

    Args:
        box: The box.
        may_get: Tells whether the hero may get a product.
        random: The source of chance.
    """
    tiers = _tiers(box, may_get)
    if not tiers:
        return box.fallback
    weights = [weight for weight, _ in tiers]
    [(_, products)] = random.choices(tiers, weights)
    return random.choice(products)


def odds(box: Box, may_get: Callable[[Product], bool]) -> dict[Product, Fraction]:
    """Return the chance of each product a box gives a hero, as pick gives them.

    Args:
        box: The box.
        may_get: Tells whether the hero may get a product.
    """
    tiers = _tiers(box, may_get)
    if not tiers:
        return {box.fallback: Fraction(1)} if box.fallback else {}
    total = sum(weight for weight, _ in tiers)
    chances: dict[Product, Fraction] = {}
    for weight, products in tiers:
        for product in products:
            share = Fraction(weight, total * len(products))
            chances[product] = chances.get(product, Fraction(0)) + share
    return chances


def below_value(box: Box) -> list[Product]:
    """Return the products a box may give that are worth less than it promises.

    Args:
        box: The box.
    """
    given = [drop.product for drop in box.drops]
    if box.fallback:
        given.append(box.fallback)
    return [product for product in given if not worth(box, product)]


def worth(box: Box, product: Product) -> bool:
    """Tell whether a product keeps a box's promise: worth at least its price, if it makes one.

    Args:
        box: The box.
        product: One of the products it may give.
    """
    return not box.worth_rule or (product.currency == box.currency and product.price >= box.value)


def _tiers(box: Box, may_get: Callable[[Product], bool]) -> list[tuple[int, list[Product]]]:
    # The tiers of the drops a hero may get, each with its weight, in the box's order.
    tiers: dict[int, tuple[int, list[Product]]] = {}
    for drop in box.drops:
        if may_get(drop.product):
            kept = tiers.setdefault(drop.tier, (drop.weight, []))[1]
            if worth(box, drop.product):
                kept.append(drop.product)
    fallback = [box.fallback] if box.fallback else []
    return [(weight, kept or fallback) for weight, kept in tiers.values() if kept or fallback]


def _owned(tag: str) -> bool:
    # A tag of an item owned for good, rather than one that names no item, such as a stack's.
    number, _, ownership = tag.partition("-")
    return ownership.startswith(PERMANENT) and number.isascii() and number.isdecimal()
