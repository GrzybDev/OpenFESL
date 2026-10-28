import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.inventory import Ownership
from openfesl.core.texts import BASE_LANGUAGE

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NewProduct:
    """A way of owning an item that a game's starting set sells, at one price in one currency.

    Attributes:
        item_id: The item's number.
        ownership: How a hero owns the item once bought.
        currency: The currency of the price.
        price: The price.
    """

    item_id: int
    ownership: Ownership
    currency: str
    price: int


@dataclass(frozen=True, slots=True)
class Product:
    """A way of owning an item that a game's store sells, at one price in one currency.

    Attributes:
        id: Its number, from which the id the game buys it by is made.
        item_id: The item's number.
        ownership: How a hero owns the item once bought.
        currency: The currency of the price.
        price: The price.
        original_price: The price before a sale, while it is on sale.
        sort: Its place among the products.
    """

    id: int
    item_id: int
    ownership: Ownership
    currency: str
    price: int
    original_price: int | None
    sort: int


@dataclass(frozen=True, slots=True)
class Offer:
    """A product as a game lists it among its store's offers.

    Attributes:
        product: The product.
        product_id: The id the game buys it by.
        name: The item's name in English, or its number where the catalogue has none.
        kind: The item's kind, by which the offers are grouped.
    """

    product: Product
    product_id: str
    name: str
    kind: str


class ProductStore(Protocol):
    """Where the products of the games' stores are kept."""

    async def stock(self, game: str, products: Sequence[NewProduct]) -> bool:
        """Store a game's starting products, unless the game has products already.

        Stores on other servers take turns, so a game is stocked once.

        Args:
            game: The game's data-file name.
            products: The products, in the order they are listed.

        Returns:
            Whether they were stored.
        """

    async def withdraw(self, game: str, items: Collection[int]) -> int:
        """Remove the products of items from a game's store.

        Args:
            game: The game's data-file name.
            items: The items' numbers.

        Returns:
            How many products were removed.
        """

    async def products(self, game: str) -> list[Product]:
        """Return the products a game's store lists, in order.

        Args:
            game: The game's data-file name.
        """

    async def product(self, game: str, number: int) -> Product | None:
        """Return a product a game's store sells to heroes, if it lists one by a number.

        Args:
            game: The game's data-file name.
            number: The product's number.
        """


class Store:
    """The games' stores, whose products their data seeds from the imported catalogues."""

    def __init__(self, store: ProductStore, catalogues: Catalogues, games: Games) -> None:
        """Keep the stores' products in a store.

        Args:
            store: Where the products are kept.
            catalogues: The games' imported catalogues, whose items the stores sell.
            games: The games the server serves.
        """
        self._store = store
        self._catalogues = catalogues
        self._games = games
        self._changes = 0

    @property
    def changes(self) -> int:
        """How often the stores' products changed while the server runs.

        What was built of them before the last change is out of date.
        """
        return self._changes

    async def stock(self) -> None:
        """Fill every store that has no products yet from its game's data, as at the start.

        A game's store fills once its catalogue was imported; later imports leave the products
        as the admin keeps them, but for those of items that the game's data withholds, which
        leave the store.
        """
        for game in self._games:
            await self._stock(game)

    def follow(self, feed: AdminFeed) -> None:
        """Fill a game's store once the admin imported its first catalogue elsewhere.

        Each import counts as a change of the stores' products. Follow the feed after the
        catalogues do, so that the store reads the new one.

        Args:
            feed: The admin's actions as the log records them.
        """
        feed.on(AdminAction.CATALOGUE_IMPORT, self._imported)

    async def offers(self, game: Game) -> list[Offer]:
        """Return the products a game's store lists, with their items' names and kinds.

        Args:
            game: The game.
        """
        if game.store is None:
            return []
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        offers = []
        for product in await self._store.products(game.id):
            item = catalogue.item(product.item_id)
            offers.append(
                Offer(
                    product,
                    game.store.product_id(product.id),
                    catalogue.name(product.item_id) or str(product.item_id),
                    item.kind if item else "",
                )
            )
        return offers

    async def _imported(self, action: LoggedAction) -> None:
        game = self._games.by_id(action.target)
        if game is not None:
            await self._stock(game)
        # The import renames the items, withdraws those withheld and may stock a store.
        self._changes += 1

    async def _stock(self, game: Game) -> None:
        rules = game.store
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        if rules is None or not catalogue.imported:
            return
        # A store stocked before the game's data withheld an item sells it no longer.
        withheld = [item.id for item in catalogue.contents.items.values() if item.withheld]
        if withheld and (removed := await self._store.withdraw(game.id, withheld)):
            logger.warning("Removed %d products of withheld items from %s", removed, game.name)
        products = [
            NewProduct(item.id, ownership, rule.currency, price)
            for item in sorted(catalogue.contents.items.values(), key=lambda item: item.id)
            # An item without a name is a leftover of the game's making.
            if rules.sellable(item) and catalogue.name(item.id)
            for rule in rules.rules(item)
            for ownership in rule.sold
            if (price := rules.price(rule, ownership))
        ]
        if products and await self._store.stock(game.id, products):
            logger.info("Stocked the store of %s with %d products", game.name, len(products))
