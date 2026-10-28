import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from openfesl.core.catalogues import Catalogues, Item
from openfesl.core.entitlements import ACTIVE, Entitlement, EntitlementStore
from openfesl.core.games import Game
from openfesl.core.hero_items import fits, granted
from openfesl.core.hero_rules import HeroRules
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.inventory import COUNTED, PERMANENT, Held, fold
from openfesl.core.personas import Persona
from openfesl.core.store import Offer, ProductStore, offer, price_order
from openfesl.core.store_rules import InGameStore, StoreCategory
from openfesl.core.wallets import Wallets

# How a hero holds an item, as the store words what is left of it.
FOR_GOOD = "permanent"
USES = "uses"
DAYS = "days"
HOURS = "hours"

_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Left:
    """What a hero has left of an item: for good, a number of uses, days or hours.

    Attributes:
        unit: permanent, uses, days or hours.
        count: The uses, days to the nearest or started hours left; 0 for good.
        until: When a rental ends.
    """

    unit: str
    count: int = 0
    until: datetime | None = None


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A product of an item, with what buying it leaves the hero.

    Attributes:
        offer: The product, as the game lists it.
        balance: What the account has of the price's currency.
        after: What the hero has left of the item once it is bought; none when buying it gains
            the hero nothing, as it owns the item for good.
    """

    offer: Offer
    balance: int
    after: Left | None

    @property
    def short(self) -> int:
        """How much of the price the balance lacks; 0 when it pays."""
        return max(self.offer.product.price - self.balance, 0)


@dataclass(frozen=True, slots=True)
class Ware:
    """An item of a category, as the in-game store shows it to a hero.

    Attributes:
        item: The item, from the game's catalogue.
        name: Its name in the store's language, else in English, else its number.
        description: The game's own text about it, in paragraphs.
        left: What the hero has left of it; none when it has none.
        low: Whether that runs low.
        equipped: Whether the hero has it equipped or wears it.
        points: How it is sold, by currency, rentals and uses before owning it for good.
    """

    item: Item
    name: str
    description: list[str]
    left: Left | None
    low: bool
    equipped: bool
    points: list[PricePoint]

    @property
    def buyable(self) -> list[PricePoint]:
        """The price points that gain the hero something, in the store's order."""
        return [point for point in self.points if point.after]

    @property
    def cheapest(self) -> list[PricePoint]:
        """The cheapest price point that gains the hero something, in each currency."""
        found: dict[str, PricePoint] = {}
        for point in self.buyable:
            cheapest = found.setdefault(point.offer.product.currency, point)
            if point.offer.product.price < cheapest.offer.product.price:
                found[point.offer.product.currency] = point
        return list(found.values())

    def point(self, product: int) -> PricePoint | None:
        """Return the price point of a product, if the item is sold as it.

        Args:
            product: The product's number.
        """
        return next((point for point in self.points if point.offer.product.id == product), None)


@dataclass(frozen=True, slots=True)
class Shelf:
    """A category of the in-game store with the items it sells a hero.

    Attributes:
        category: The category.
        wares: Its items that fit the hero, in the store's order.
    """

    category: StoreCategory
    wares: list[Ware]

    def ware(self, item_id: int) -> Ware | None:
        """Return one of the category's items, if it sells it to the hero.

        Args:
            item_id: The item's number.
        """
        return next((ware for ware in self.wares if ware.item.id == item_id), None)


@dataclass(frozen=True, slots=True)
class StoreWindow:
    """The in-game store as a hero sees it: the hero, the account's balances and the categories.

    Attributes:
        rules: What the store sells and how.
        hero: The hero who buys.
        balances: What the account has of each currency, by code.
        shelves: The categories, in order.
    """

    rules: InGameStore
    hero: Hero
    balances: Mapping[str, int]
    shelves: list[Shelf]

    def shelf(self, key: str) -> Shelf | None:
        """Return a category by its name, if the store has one.

        Args:
            key: The category's name.
        """
        return next((shelf for shelf in self.shelves if shelf.category.key == key), None)


class StoreWindows:
    """Opens the store that a game shows in its own browser, for one of an account's heroes."""

    def __init__(
        self,
        products: ProductStore,
        catalogues: Catalogues,
        heroes: Heroes,
        entitlements: EntitlementStore,
        wallets: Wallets,
    ) -> None:
        """Read the store's products, the heroes and what they hold from their stores.

        Args:
            products: Where the products are kept.
            catalogues: The games' imported catalogues, whose items the store sells.
            heroes: The heroes, with their factions, classes, levels and worn items.
            entitlements: Where the items the heroes hold are kept.
            wallets: The balances the account pays with.
        """
        self._products = products
        self._catalogues = catalogues
        self._heroes = heroes
        self._entitlements = entitlements
        self._wallets = wallets

    async def open(
        self,
        game: Game,
        persona: Persona,
        language: str,
        equipped: Sequence[int] = (),
        uses: Mapping[int, int] | None = None,
    ) -> StoreWindow | None:
        """Return what a game's in-game store sells one of its heroes, by category.

        Args:
            game: The hero's game.
            persona: The hero, which the asking account owns.
            language: The language of the item names, such as en.
            equipped: Items the game says the hero has equipped, in its order, besides those its
                stats name.
            uses: The uses left that the game counts in the running round, by item; fewer than
                the stored ones, as uses spent in a round are stored at its end.

        Returns:
            The store, or None when the game has none or the hero is not one it can play.
        """
        store = game.store
        rules = store.in_game if store else None
        if store is None or rules is None or game.heroes is None:
            return None
        roster = await self._heroes.roster(game, persona.account_id)
        hero = next((found for found in roster.heroes if found.persona.id == persona.id), None)
        if hero is None or hero.faction is None or hero.hero_class is None:
            return None
        now = datetime.now(UTC)
        # The game grants a hero its loadout itself, but for the consumables (BFHeroes.exe 1.89
        # 0x007b9deb), which the store sells as uses.
        loadout = granted(game.heroes, hero.faction, hero.hero_class)
        catalogue = await self._catalogues.of(game, language)
        balances = await self._wallets.of_persona(game, persona)
        # The game's list is newer than the hero's stats, and buying and equipping adds to its
        # end: the last item named of a slot is the one worn there.
        worn: dict[str | int, int] = {}
        for named in [*await self._heroes.worn(game, persona.id), *equipped]:
            item = catalogue.item(named)
            worn[item.slot if item and item.slot else named] = named
        holdings = {
            number: (entitlement.status, _held(entitlement))
            for entitlement in await self._entitlements.of_persona(persona.id)
            if (number := _item(entitlement.tag)) is not None
        }
        kinds = {kind for category in rules.categories for kind in category.kinds}
        sold: dict[int, list[Offer]] = {}
        for product in await self._products.products(game.id):
            item = catalogue.item(product.item_id)
            if (
                product.currency in rules.currencies
                and item is not None
                and item.kind in kinds
                and fits(item, {item.kind}, hero.faction, hero.hero_class, hero.level)
                and holdings.get(item.id, (ACTIVE, None))[0] == ACTIVE
            ):
                sold.setdefault(item.id, []).append(offer(game, catalogue, product))
        wares: list[Ware] = []
        for item_id, offers in sold.items():
            item = catalogue.item(item_id)
            assert item is not None
            held = holdings.get(item_id, (ACTIVE, None))[1]
            counted = any(found.product.ownership.type == COUNTED for found in offers)
            if held is None and item_id in loadout and not counted:
                held = Held(PERMANENT)
            if held and held.type == COUNTED and item_id in (uses or {}):
                held = Held(COUNTED, None, min(held.uses or 0, (uses or {})[item_id]))
            left = left_of(held, now)
            points = [
                PricePoint(
                    found,
                    balances.get(found.product.currency, 0),
                    left_of(fold(held, found.product.ownership, now), now),
                )
                for found in sorted(
                    offers, key=lambda found: price_order(rules.currencies, found.product)
                )
            ]
            wares.append(
                Ware(
                    item=item,
                    name=catalogue.name(item_id) or str(item_id),
                    description=list(store.describe(catalogue, item_id)),
                    left=left,
                    low=runs_low(left, game.heroes),
                    equipped=item_id in worn.values(),
                    points=points,
                )
            )
        shelves = [
            Shelf(category, [ware for ware in wares if ware.item.kind in category.kinds])
            for category in rules.categories
        ]
        return StoreWindow(rules, hero, balances, shelves)


def running_low(rules: HeroRules, kept: Iterable[Entitlement], now: datetime) -> int:
    """Return how many of the items kept for a hero run low, as the in-game store marks them.

    Args:
        rules: The game's hero rules, which say when an item runs low.
        kept: The entitlements kept for the hero; those naming no item, such as a stack of
            treasure chests, are left out.
        now: The time that what is left counts from.
    """
    return sum(
        1
        for entry in kept
        if entry.status == ACTIVE
        and _item(entry.tag) is not None
        and runs_low(left_of(_held(entry), now), rules)
    )


def _item(tag: str) -> int | None:
    # The item a tag names; none for what is neither worn nor used, such as a treasure chest.
    number = tag.partition("-")[0]
    return int(number) if number.isascii() and number.isdecimal() else None


def _held(entitlement: Entitlement) -> Held:
    # How an entitlement holds its item, as the store folds what a hero buys into it.
    return Held(entitlement.tag.partition("-")[2], entitlement.expires, entitlement.uses)


def left_of(held: Held | None, now: datetime) -> Left | None:
    """Return what a hero has left of an item it holds; none when nothing is left.

    Args:
        held: How the hero holds the item; none when it does not.
        now: The time that what is left counts from.
    """
    if held is None:
        return None
    if held.type == COUNTED:
        return Left(USES, held.uses or 0) if held.uses else None
    if held.expires is None:
        return Left(FOR_GOOD)
    remaining = held.expires - now
    if remaining <= timedelta():
        return None
    hours = math.ceil(remaining / timedelta(hours=1))
    if hours < 24:
        return Left(HOURS, hours, held.expires)
    # To the nearest day, so that a rental bought a moment ago shows all its days.
    return Left(DAYS, (remaining + _DAY / 2) // _DAY, held.expires)


def runs_low(left: Left | None, rules: HeroRules) -> bool:
    """Tell whether what a hero has left of an item runs low, as the in-game store marks it.

    Args:
        left: What the hero has left; none when it has nothing.
        rules: The game's hero rules, which say when an item runs low.
    """
    if left is None:
        return False
    return (
        (left.unit == USES and left.count <= rules.running_low)
        or (left.unit == DAYS and left.count <= rules.running_low_days)
        or left.unit == HOURS
    )
