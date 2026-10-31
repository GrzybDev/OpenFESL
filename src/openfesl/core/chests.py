from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, PositiveInt

from openfesl.core.inventory import COUNTED, Ownership

# For annotations only, since the games' data holds the rules below.
if TYPE_CHECKING:
    from openfesl.core.drops import Box, Drops
    from openfesl.core.games import Game
    from openfesl.core.heroes import Heroes
    from openfesl.core.personas import Persona
    from openfesl.core.store import Product, ProductStore


class ChestRules(BaseModel):
    """How a game's dedicated servers stack treasure chests on heroes, and keys open them.

    Attributes:
        tag: The tag of a hero's stack of the chests it picked up.
        most: The most chests a server stacks on a hero at once.
        key: The item number of the key that opens one chest, which the account holds.
        key_price: What a key costs.
        currency: The currency keys are bought with, which the account holds.
        contents: The key of the store's box that gives a chest's contents.
        packs: The numbers of keys the site sells at once, each for the key's price that many
            times.
    """

    model_config = ConfigDict(frozen=True)

    tag: str
    most: PositiveInt
    key: PositiveInt
    key_price: PositiveInt
    currency: str
    contents: str
    packs: list[PositiveInt] = [1]


class ChestRefusal(StrEnum):
    """Why a chest was not opened.

    No key, no chest, nothing in it the hero may get, or opening switched off by the admin.
    """

    NO_KEY = "no-key"
    NO_CHEST = "no-chest"
    NOTHING = "nothing"
    CLOSED = "closed"


class ChestRefused(Exception):
    """Raised for a chest that was not opened; no key, chest or item changed."""

    def __init__(self, refusal: ChestRefusal) -> None:
        """Say why the chest was not opened.

        Args:
            refusal: Why.
        """
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class ChestDrop:
    """One of the things the chests of a faction's heroes give, with its chance.

    Attributes:
        products: The product, or the products owned alike of which a hero gets those that fit
            its class.
        chance: The lowest and the highest chance among the faction's classes.
    """

    products: tuple[Product, ...]
    chance: tuple[Fraction, Fraction]


@dataclass(frozen=True, slots=True)
class ChestOdds:
    """What a game's treasure chests give the heroes of each faction, with the chances.

    Attributes:
        worth: What every item a chest gives is worth at least; none where chests do not
            promise it.
        currency: The currency of that worth.
        factions: The drops by faction, in the order of the game's factions.
    """

    worth: int | None
    currency: str
    factions: dict[str, list[ChestDrop]]


class ChestStore(Protocol):
    """Where the keys and chests are kept and used up."""

    async def open_chest(self, persona: Persona, key: str, chest: str, won: Product) -> None:
        """Use up one of an account's keys and one of its hero's chests, and give what it held.

        All changes are made or none.

        Args:
            persona: The hero.
            key: The tag of the account's keys.
            chest: The tag of the hero's chests.
            won: What the chest gives.

        Raises:
            ChestRefused: When the account has no key or the hero no chest, also after another
                opening at the same time, or the hero came to own the item for good meanwhile.
        """


class Chests:
    """Opens heroes' treasure chests with their accounts' keys, and tells what chests give."""

    def __init__(
        self, products: ProductStore, store: ChestStore, heroes: Heroes, drops: Drops
    ) -> None:
        """Open chests with what stores keep.

        Args:
            products: Where the store's boxes are kept, the chests' contents among them.
            store: Where the keys and chests are kept.
            heroes: The heroes, with their factions, classes and levels.
            drops: Picks what a chest gives a hero.
        """
        self._products = products
        self._store = store
        self._heroes = heroes
        self._drops = drops

    async def open(self, game: Game, persona: Persona) -> Product:
        """Open one of a hero's chests with one of its account's keys.

        Args:
            game: The hero's game, which has chests.
            persona: The hero.

        Returns:
            What the chest gave the hero, which it now holds.

        Raises:
            ChestRefused: When the account has no key, the hero no chest, the chest holds
                nothing the hero may get, or the admin switched opening off.
        """
        rules = game.chests
        box = await self._products.box_by_key(game.id, rules.contents) if rules else None
        if box is not None and not box.enabled:
            raise ChestRefused(ChestRefusal.CLOSED)
        roster = await self._heroes.roster(game, persona.account_id)
        hero = next((hero for hero in roster.heroes if hero.persona.id == persona.id), None)
        won = await self._drops.roll(game, box, persona, hero) if box and hero else None
        if rules is None or won is None:
            raise ChestRefused(ChestRefusal.NOTHING)
        await self._store.open_chest(persona, key_tag(rules), rules.tag, won)
        return won

    async def closed(self, game: Game) -> bool:
        """Return whether the admin switched the opening of a game's chests off.

        Args:
            game: The game.
        """
        rules = game.chests
        box = await self._products.box_by_key(game.id, rules.contents) if rules else None
        return box is not None and not box.enabled

    async def key(self, game: Game) -> Product | None:
        """Return the product that sells an account a game's keys, if its store sells them.

        Args:
            game: The game.
        """
        rules = game.chests
        return await self._products.account_product(game.id, rules.key) if rules else None

    async def worth(self, game: Game) -> tuple[int, str] | None:
        """Return what every item a game's chest gives is worth at least, where chests promise it.

        Args:
            game: The game.

        Returns:
            The amount and its currency, or None for a game whose chests make no such promise.
        """
        rules = game.chests
        box = await self._products.box_by_key(game.id, rules.contents) if rules else None
        return (box.value, box.currency) if box and box.worth_rule else None

    async def odds(self, game: Game) -> ChestOdds | None:
        """Return what a chest gives the heroes of each faction, with the chances.

        The chances are those of a hero that owns none of the items, as a chest gives a hero no
        item it owns for good.

        Args:
            game: The game.

        Returns:
            The odds, or None for a game without chests, heroes or a box of the chests' contents.
        """
        rules, heroes = game.chests, game.heroes
        box = await self._products.box_by_key(game.id, rules.contents) if rules else None
        if box is None or heroes is None:
            return None
        factions = {
            faction: _drops(
                box, [self._drops.odds(game, box, faction, kind) for kind in heroes.classes]
            )
            for faction in heroes.factions
        }
        return ChestOdds(box.value if box.worth_rule else None, box.currency, factions)


def key_tag(rules: ChestRules) -> str:
    """Return the tag of an account's keys, which it holds as uses of the key item.

    Args:
        rules: The game's chest rules.
    """
    return Ownership(COUNTED).tag(rules.key)


def _drops(box: Box, by_class: list[Mapping[Product, Fraction]]) -> list[ChestDrop]:
    # A product that every class may get is a drop of its own; those for some classes join the
    # others owned alike, so that each class's chances add up to the whole.
    given = [drop.product for drop in box.drops] + ([box.fallback] if box.fallback else [])
    groups: dict[object, list[Product]] = {}
    for product in dict.fromkeys(given):
        classes = sum(product in chances for chances in by_class)
        if classes:
            key = product if classes == len(by_class) else product.ownership
            groups.setdefault(key, []).append(product)
    drops = []
    for products in groups.values():
        chances = [
            sum((odds.get(item, Fraction(0)) for item in products), Fraction(0))
            for odds in by_class
        ]
        drops.append(ChestDrop(tuple(products), (min(chances), max(chances))))
    return drops
