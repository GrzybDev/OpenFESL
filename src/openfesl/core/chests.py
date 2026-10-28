from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, PositiveInt

from openfesl.core.inventory import COUNTED, Ownership

# For annotations only, since the games' data holds the rules below.
if TYPE_CHECKING:
    from openfesl.core.drops import Drops
    from openfesl.core.games import Game
    from openfesl.core.heroes import Heroes
    from openfesl.core.personas import Persona
    from openfesl.core.store import Product, ProductStore


class ChestRules(BaseModel):
    """How a game's dedicated servers stack treasure chests on heroes, and keys open them.

    Attributes:
        tag: The tag of a hero's stack of the chests it picked up.
        key: The item number of the key that opens one chest, which the account holds.
        key_price: What a key costs.
        currency: The currency keys are bought with, which the account holds.
        contents: The key of the store's box that gives a chest's contents.
    """

    model_config = ConfigDict(frozen=True)

    tag: str
    key: PositiveInt
    key_price: PositiveInt
    currency: str
    contents: str


class ChestRefusal(StrEnum):
    """Why a chest was not opened: no key, no chest, or nothing in it the hero may get."""

    NO_KEY = "no-key"
    NO_CHEST = "no-chest"
    NOTHING = "nothing"


class ChestRefused(Exception):
    """Raised for a chest that was not opened; no key, chest or item changed."""

    def __init__(self, refusal: ChestRefusal) -> None:
        """Say why the chest was not opened.

        Args:
            refusal: Why.
        """
        super().__init__(refusal.value)
        self.refusal = refusal


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
    """Opens the treasure chests that heroes pick up, each with a key of their account."""

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
            ChestRefused: When the account has no key, the hero no chest, or the chest holds
                nothing the hero may get.
        """
        rules = game.chests
        box = await self._products.box_by_key(game.id, rules.contents) if rules else None
        roster = await self._heroes.roster(game, persona.account_id)
        hero = next((hero for hero in roster.heroes if hero.persona.id == persona.id), None)
        won = await self._drops.roll(game, box, persona, hero) if box and hero else None
        if rules is None or won is None:
            raise ChestRefused(ChestRefusal.NOTHING)
        await self._store.open_chest(persona, key_tag(rules), rules.tag, won)
        return won


def key_tag(rules: ChestRules) -> str:
    """Return the tag of an account's keys, which it holds as uses of the key item.

    Args:
        rules: The game's chest rules.
    """
    return Ownership(COUNTED).tag(rules.key)
