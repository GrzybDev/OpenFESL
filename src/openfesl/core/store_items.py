from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from openfesl.core.catalogues import Catalogues, Item
from openfesl.core.entitlements import ACTIVE, Entitlement, EntitlementStore
from openfesl.core.games import Game, Scope
from openfesl.core.hero_items import held
from openfesl.core.heroes import Hero
from openfesl.core.inventory import PERMANENT, Held, Ownership
from openfesl.core.store import ProductStore, price_order
from openfesl.core.wallets import Wallets


class Unfit(StrEnum):
    """Why a hero may not have what a store page sells.

    Its faction, class or level, an ability's level before the one sold, which it lacks, or its
    own item that the game or an admin took out of use, to which a purchase adds nothing.
    """

    FACTION = "faction"
    CLASS = "class"
    LEVEL = "level"
    ORDER = "order"
    TAKEN = "taken"


@dataclass(frozen=True, slots=True)
class Requirement:
    """Which heroes may have what a store page sells.

    Attributes:
        factions: The factions whose heroes may; any when empty.
        classes: The classes that may; any when empty.
        level: The lowest level that may.
    """

    factions: frozenset[str] = frozenset()
    classes: frozenset[str] = frozenset()
    level: int = 1

    def unfit(self, hero: Hero) -> Unfit | None:
        """Return why a hero may not have it, faction and class before level; none if it may.

        Args:
            hero: The hero.
        """
        if self.factions and hero.faction not in self.factions:
            return Unfit.FACTION
        if self.classes and hero.hero_class not in self.classes:
            return Unfit.CLASS
        return Unfit.LEVEL if hero.level < self.level else None


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One way of buying what a store page sells, as the store sells it.

    Attributes:
        number: The number of the product that is bought.
        ownership: How the hero owns the item.
        currency: The currency of the price.
        price: The price.
    """

    number: int
    ownership: Ownership
    currency: str
    price: int


@dataclass(frozen=True, slots=True)
class StoreItem:
    """An item that a page of a game's store sells alone, named in a language.

    Attributes:
        number: The number the page is found by.
        name: Its name.
        item: The item.
        requirement: Which heroes may have it.
        prices: The ways of buying it.
    """

    number: int
    name: str
    item: Item
    requirement: Requirement
    prices: tuple[PricePoint, ...]


@dataclass(frozen=True, slots=True)
class HeroChoice:
    """One of an account's heroes as a store page offers it what it sells.

    Attributes:
        hero: The hero.
        unfit: Why it may not have it; none if it may.
        held: How it holds the item, if it does; for good when the game grants it
            the item itself.
        balances: What it has to pay each of the prices with, by currency.
    """

    hero: Hero
    unfit: Unfit | None
    held: Held | None
    balances: Mapping[str, int]

    @property
    def owned(self) -> bool:
        """Whether it owns the item for good, so that buying gains it nothing."""
        return self.held is not None and self.held.type == PERMANENT


class StoreItems:
    """The pages of the games' stores: what each sells, and what each hero may buy of it."""

    def __init__(
        self,
        products: ProductStore,
        catalogues: Catalogues,
        entitlements: EntitlementStore,
        wallets: Wallets,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Read the stores' products, and the heroes' items and wallets.

        Args:
            products: Where the products are kept.
            catalogues: The games' imported catalogues, which name the items and say who fits.
            entitlements: Where the items the heroes hold are kept.
            wallets: The balances the heroes pay with.
            clock: Tells the time; the system's own when none.
        """
        self._products = products
        self._catalogues = catalogues
        self._entitlements = entitlements
        self._wallets = wallets
        self._clock = clock or (lambda: datetime.now(UTC))

    async def find(self, game: Game, number: int, language: str) -> StoreItem | None:
        """Return the item that a game's store sells alone by a number, named in a language.

        A product's number finds its item with every product the item is sold alone by.

        Args:
            game: The game, which has a store and heroes.
            number: The number of a product.
            language: The language code; English where the texts lack it.

        Returns:
            What the page sells; none for a number the store sells no item by to heroes.
        """
        if game.store is None or game.heroes is None:
            return None
        catalogue = await self._catalogues.of(game, language)
        product = await self._products.product(game.id, number)
        item = catalogue.item(product.item_id) if product else None
        if product is None or item is None:
            return None
        sold = await self._products.of_item(game.id, product.item_id)
        sold.sort(key=lambda found: price_order(list(game.currencies), found))
        return StoreItem(
            number=number,
            name=catalogue.name(item.id) or str(item.id),
            item=item,
            requirement=_requirement(game, item),
            prices=tuple(
                PricePoint(found.id, found.ownership, found.currency, found.price) for found in sold
            ),
        )

    async def choices(
        self, game: Game, sold: StoreItem, account_id: int, heroes: Sequence[Hero]
    ) -> list[HeroChoice]:
        """Return an account's heroes as a store page offers them what it sells.

        Args:
            game: The game, which has heroes.
            sold: What the page sells.
            account_id: The account.
            heroes: The account's heroes.
        """
        rules = game.heroes
        assert rules is not None
        currencies = list(dict.fromkeys(price.currency for price in sold.prices))
        shared = [code for code in currencies if game.currencies[code].scope is Scope.ACCOUNT]
        balances = dict(
            zip(shared, await self._wallets.of_account(game, account_id, shared), strict=True)
        )
        own = {
            code: await self._wallets.of_personas(game, [hero.persona for hero in heroes], code)
            for code in currencies
            if code not in balances
        }
        now, item, choices = self._clock(), sold.item, []
        for index, hero in enumerate(heroes):
            rows = await self._entitlements.of_persona(hero.persona.id)
            kept = [
                entry
                for entry in rows
                if entry.status == ACTIVE and (entry.expires is None or entry.expires > now)
            ]
            unfit = sold.requirement.unfit(hero)
            holds = held(rules, hero.faction, hero.hero_class, kept)
            if unfit is None and item.id in item.levels[1:]:
                previous = item.levels[item.levels.index(item.id) - 1]
                unfit = None if previous in holds else Unfit.ORDER
            if unfit is None and item:
                # The game or an admin took the hero's own item out of use; the store adds
                # nothing to it.
                prefix = f"{item.id}-"
                if any(entry.tag.startswith(prefix) and entry.status != ACTIVE for entry in rows):
                    unfit = Unfit.TAKEN
            hers = balances | {code: found[index][0] for code, found in own.items()}
            choices.append(HeroChoice(hero, unfit, _held(item, holds, kept), hers))
        return choices


def _requirement(game: Game, item: Item) -> Requirement:
    # An item that fits every faction or class of the game asks for none.
    rules = game.heroes
    assert rules is not None
    factions = item.factions if not set(rules.factions) <= item.factions else frozenset()
    classes = item.classes if not set(rules.classes) <= item.classes else frozenset()
    return Requirement(factions, classes, item.level or 1)


def _held(item: Item, holds: set[int], kept: list[Entitlement]) -> Held | None:
    # How a hero holds an item: as its entitlement in use says, or for good when the game
    # grants it the item itself, as its faction and class's loadout or a base item.
    if item.id not in holds and not item.base:
        return None
    prefix = f"{item.id}-"
    for entry in kept:
        if entry.tag.startswith(prefix):
            return Held(entry.tag.removeprefix(prefix), entry.expires, entry.uses)
    return Held(PERMANENT)
