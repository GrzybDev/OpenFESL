import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.drops import Box
from openfesl.core.games import Game, Games
from openfesl.core.inventory import COUNTED, Ownership
from openfesl.core.store_rules import BoxRule
from openfesl.core.texts import BASE_LANGUAGE

logger = logging.getLogger(__name__)

# Who made a bundle: the admin, or the hot deals' rotation by itself.
ADMIN = "admin"
AUTO = "auto"


@dataclass(frozen=True, slots=True)
class NewProduct:
    """A way of owning an item that a game's starting set sells, at one price in one currency.

    Attributes:
        item_id: The item's number.
        ownership: How a hero owns the item once bought.
        currency: The currency of the price.
        price: The price.
        published: Whether it is sold alone, rather than only as a box's drop.
        account: Whether the account owns it rather than one of its heroes.
    """

    item_id: int
    ownership: Ownership
    currency: str
    price: int
    published: bool = True
    account: bool = False


@dataclass(frozen=True, slots=True)
class NewBox:
    """A box that a game's starting set holds, with its drops among the starting products.

    Attributes:
        rule: What the game's data says of the box.
        drops: Its drops, each with the number of its tier, from 0.
        fallback: What it gives when a hero may get none of its drops.
    """

    rule: BoxRule
    drops: tuple[tuple[int, NewProduct], ...]
    fallback: NewProduct | None


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
        published: Whether it is sold alone, rather than only as a part of bundles and boxes.
    """

    id: int
    item_id: int
    ownership: Ownership
    currency: str
    price: int
    original_price: int | None
    sort: int
    published: bool = True


@dataclass(frozen=True, slots=True)
class Bundle:
    """Products that a game's store sells together at one price, as a hot deal, for a while.

    Attributes:
        name: Its name, as the id of a text of the game's language table or as the text itself.
        description: What the game shows about it, likewise.
        currency: The currency of its price.
        price: Its price.
        original_price: Its price before a sale, while it is on sale.
        faction: The faction whose heroes may buy it; any when none.
        hero_class: The class whose heroes may buy it; any when none.
        game_order: Its place among the hot deals.
        starts: Since when it is sold; from its making when none.
        ends: Until when it is sold; for good when none.
        members: The numbers of the products it holds, which need not be sold alone.
        box: The number of the box of which it gives one product at random, if it is one.
        section: The name of the hot deals' section it is listed in; after them when none.
        pool: What the rotation picked it from, such as a set's name, which a later period
            of its section does not pick again for a while.
        source: Who made it: the admin, or the rotation by itself.
    """

    name: str
    description: str | None
    currency: str
    price: int
    original_price: int | None = None
    faction: str | None = None
    hero_class: str | None = None
    game_order: int = 0
    starts: datetime | None = None
    ends: datetime | None = None
    members: tuple[int, ...] = ()
    box: int | None = None
    section: str | None = None
    pool: str | None = None
    source: str = ADMIN


@dataclass(frozen=True, slots=True)
class ListedBundle:
    """A bundle that a game's store sells now.

    Attributes:
        id: Its number, from which the id the game buys it by is made.
        created: When it was made.
        bundle: What it sells, for what and to whom.
        members: The products it holds.
    """

    id: int
    created: datetime
    bundle: Bundle
    members: list[Product]


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

    async def stock(
        self, game: str, products: Sequence[NewProduct], boxes: Sequence[NewBox]
    ) -> bool:
        """Store a game's starting products and boxes, unless the game has products already.

        Stores on other servers take turns, so a game is stocked once.

        Args:
            game: The game's data-file name.
            products: The products, in the order they are listed.
            boxes: The boxes, whose drops are among the products.

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

    async def product(self, game: str, number: int, account: bool = False) -> Product | None:
        """Return a product a game's store sells, if it lists one by a number.

        Args:
            game: The game's data-file name.
            number: The product's number.
            account: Whether it is sold to accounts rather than to heroes.
        """

    async def add_bundle(self, game: str, bundle: Bundle) -> int:
        """Add a bundle to a game's store.

        Args:
            game: The game's data-file name.
            bundle: The bundle, whose products the store holds.

        Returns:
            The bundle's number.
        """

    async def bundles(self, game: str, now: datetime) -> list[ListedBundle]:
        """Return the bundles a game's store sells at a moment, by their order among the hot deals.

        Args:
            game: The game's data-file name.
            now: The moment.
        """

    async def box(self, game: str, number: int) -> Box | None:
        """Return a box of a game's store with its drops, if it has one by a number.

        Args:
            game: The game's data-file name.
            number: The box's number.
        """

    async def box_by_key(self, game: str, key: str) -> Box | None:
        """Return a box of a game's store with its drops, if it has one by a key.

        Args:
            game: The game's data-file name.
            key: The key the game's data knows the box by.
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
        return [offer(game, catalogue, product) for product in await self._store.products(game.id)]

    async def add_bundle(self, game: Game, bundle: Bundle) -> int:
        """Add a bundle to a game's store, to sell its products together as a hot deal.

        Args:
            game: The game.
            bundle: The bundle, whose products the store holds.

        Returns:
            The bundle's number.
        """
        return await self._store.add_bundle(game.id, bundle)

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
        sold = {(product.item_id, product.ownership, product.currency) for product in products}

        def drop(item_id: int, ownership: Ownership, currency: str) -> NewProduct | None:
            item = catalogue.item(item_id)
            price = rules.price_of(item, ownership, currency) if item else None
            if item is None or price is None or not rules.sellable(item):
                return None
            # A drop that is not sold alone is only given by boxes.
            published = (item_id, ownership, currency) in sold
            return NewProduct(item_id, ownership, currency, price, published)

        boxes = []
        for box in rules.boxes:
            drops = tuple(
                (tier, found)
                for tier, rule in enumerate(box.tiers)
                for item_id, ownership in rule.drops
                if (found := drop(item_id, ownership, box.currency))
            )
            fallback = drop(*box.fallback, box.currency) if box.fallback else None
            if drops or fallback:
                boxes.append(NewBox(box, drops, fallback))
        given = {found for new in boxes for _, found in new.drops} | {
            new.fallback for new in boxes if new.fallback
        }
        products += sorted(
            (found for found in given if not found.published), key=lambda found: found.item_id
        )
        # A key, which the game has no definition of, is sold to the account alone.
        if chests := game.chests:
            key = Ownership(COUNTED, uses=1)
            products.append(
                NewProduct(chests.key, key, chests.currency, chests.key_price, account=True)
            )
        if products and await self._store.stock(game.id, products, boxes):
            logger.info("Stocked the store of %s with %d products", game.name, len(products))


def offer(game: Game, catalogue: Catalogue, product: Product) -> Offer:
    """Return a product as a game lists it, named from a catalogue's texts.

    Args:
        game: The game.
        catalogue: The game's catalogue, with its texts in the language listed in.
        product: The product.
    """
    item = catalogue.item(product.item_id)
    return Offer(
        product,
        game.store.product_id(product.id) if game.store else str(product.id),
        catalogue.name(product.item_id) or str(product.item_id),
        item.kind if item else "",
    )
