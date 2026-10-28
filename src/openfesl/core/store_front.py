from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime

from openfesl.core.catalogues import Catalogues, Item
from openfesl.core.entitlements import ACTIVE, EntitlementStore
from openfesl.core.games import Game
from openfesl.core.hero_items import granted, held, holding
from openfesl.core.heroes import Hero
from openfesl.core.inventory import COUNTED
from openfesl.core.store import Product, ProductStore, price_order
from openfesl.core.store_items import Requirement, requirement
from openfesl.core.store_window import Left, left_of, runs_low
from openfesl.core.texts import BASE_LANGUAGE


@dataclass(frozen=True, slots=True)
class FrontItem:
    """An item that a game's store sells alone, as its store front lists it.

    Attributes:
        item: The item, from the game's catalogue.
        number: The number of its first price point, by which its page opens.
        prices: Its lowest price in each currency it is sold for, as currency and price, in the
            order of the site's wallets, then of the game's currencies.
        requirement: Which heroes may have it.
        uses: Whether it is sold as uses, which the game does not grant a hero itself even
            where it is of the hero's loadout.
    """

    item: Item
    number: int
    prices: tuple[tuple[str, int], ...]
    requirement: Requirement
    uses: bool


@dataclass(frozen=True, slots=True)
class Owned:
    """An item a hero holds, as the store front's inventory lists it.

    Attributes:
        left: What the hero has left of it.
        low: Whether that runs low.
        granted: Whether the game grants it the hero itself, as its faction and class's loadout.
    """

    left: Left
    low: bool
    granted: bool


class StoreFront:
    """What a game's store sells alone, and what each hero holds of it, for its store front."""

    def __init__(
        self,
        products: ProductStore,
        catalogues: Catalogues,
        entitlements: EntitlementStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Read the stores' products, the catalogues and the heroes' items.

        Args:
            products: Where the products are kept.
            catalogues: The games' imported catalogues, whose items the stores sell.
            entitlements: Where the items the heroes hold are kept.
            clock: Tells the time; the system's own when none.
        """
        self._products = products
        self._catalogues = catalogues
        self._entitlements = entitlements
        self._clock = clock or (lambda: datetime.now(UTC))

    async def items(self, game: Game) -> list[FrontItem]:
        """Return the items that a game's store sells alone to heroes, in the store's order.

        An item counts while at least one of its products is listed; withheld items never do.
        Prices in the heroes' points are left out, as the game's own menu spends them.

        Args:
            game: The game, which has a store and heroes.
        """
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        points = game.heroes.points if game.heroes else None
        sold: dict[int, list[Product]] = {}
        for product in await self._products.products(game.id):
            item = catalogue.item(product.item_id)
            if item is not None and not item.withheld and product.currency != points:
                sold.setdefault(item.id, []).append(product)
        currencies = list(game.currencies)
        wallets = [wallet.currency for wallet in game.site.wallets] if game.site else []
        listed = wallets + [code for code in currencies if code not in wallets]
        found = []
        for item_id, products in sold.items():
            item = catalogue.item(item_id)
            assert item is not None
            lowest: dict[str, int] = {}
            for product in products:
                lowest[product.currency] = min(
                    lowest.get(product.currency, product.price), product.price
                )
            first = min(products, key=lambda product: price_order(currencies, product))
            prices = tuple(sorted(lowest.items(), key=lambda price: listed.index(price[0])))
            uses = any(product.ownership.type == COUNTED for product in products)
            found.append(FrontItem(item, first.id, prices, requirement(game, item), uses))
        return found

    async def matching(self, game: Game, query: str) -> set[int]:
        """Return the items whose name in any imported language holds a text, or whose number it is.

        Args:
            game: The game.
            query: The text, compared ignoring capitals.
        """
        wanted = query.casefold()
        contents = self._catalogues.contents(game)
        found = {int(query)} if query.isascii() and query.isdecimal() else set()
        for language in contents.languages or (BASE_LANGUAGE,):
            catalogue = await self._catalogues.of(game, language)
            found |= {
                item_id
                for item_id in contents.items
                if wanted in (catalogue.name(item_id) or "").casefold()
            }
        return found

    async def holdings(self, game: Game, hero: Hero, counted: Collection[int]) -> dict[int, Owned]:
        """Return the items a hero holds with what it has left of each, by item.

        Args:
            game: The game, which has heroes.
            hero: The hero.
            counted: The items the store sells as uses, which the game does not grant a hero
                itself even where they are of its loadout, so that it holds them only while
                it has uses left.
        """
        rules = game.heroes
        assert rules is not None
        now = self._clock()
        kept = [
            entry
            for entry in await self._entitlements.of_persona(hero.persona.id)
            if entry.status == ACTIVE and (entry.expires is None or entry.expires > now)
        ]
        tagged = {entry.tag.partition("-")[0] for entry in kept}
        loadout = granted(rules, hero.faction, hero.hero_class)
        owned = {}
        for item_id in held(rules, hero.faction, hero.hero_class, kept):
            alone = str(item_id) not in tagged
            if alone and item_id in counted:
                continue
            left = left_of(holding(item_id, kept), now)
            if left is not None:
                owned[item_id] = Owned(left, runs_low(left, rules), alone and item_id in loadout)
        return owned
