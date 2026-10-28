from dataclasses import dataclass
from typing import Protocol

from openfesl.core.catalogues import Catalogues
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game
from openfesl.core.hero_items import fits, granted, held
from openfesl.core.heroes import Heroes
from openfesl.core.inventory import Ownership, gains
from openfesl.core.magma import MagmaError
from openfesl.core.personas import Persona
from openfesl.core.store import ProductStore
from openfesl.core.wallets import Wallet, wallet_of

# The code of a refused purchase and its causes, which the game only logs: a product it does
# not sell, an item the hero may not use, an ability level before the one below it, an item the
# hero owns for good or that was taken from it, and too little to pay with.
PURCHASE_REFUSED = "PURCHASE_REFUSED"
UNKNOWN_PRODUCT = "UNKNOWN_PRODUCT"
NOT_FOR_HERO = "NOT_FOR_HERO"
LEVEL_MISSING = "LEVEL_MISSING"
ALREADY_OWNED = "ALREADY_OWNED"
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"


class PurchaseRefused(MagmaError):
    """Refuses a purchase, of which nothing was stored or spent."""

    def __init__(self, cause: str) -> None:
        """Refuse a purchase.

        Args:
            cause: Why.
        """
        super().__init__(PURCHASE_REFUSED, cause)


@dataclass(frozen=True, slots=True)
class Purchase:
    """A purchase to make at once: the price paid and the items a hero gains for it.

    Attributes:
        game: The game's data-file name.
        persona: The hero.
        product_id: The id the game bought it by.
        kind: What was bought: a product or a bundle.
        wallet: The wallet the price is paid from.
        price: The price.
        items: The items the hero gains, by number, with how it owns each; those it owns for
            good are left out, and a purchase that gains it none is refused.
    """

    game: str
    persona: Persona
    product_id: str
    kind: str
    wallet: Wallet
    price: int
    items: tuple[tuple[int, Ownership], ...]


class PurchaseStore(Protocol):
    """Where purchases are paid, given and recorded."""

    async def buy(self, purchase: Purchase) -> None:
        """Pay a purchase's price, give the hero its items and record it, all or nothing.

        Args:
            purchase: The purchase.

        Raises:
            PurchaseRefused: When the hero gains no item, as it owns them for good or they were
                taken away, or the wallet holds less than the price, also after another
                purchase at the same time.
        """


class Purchases:
    """Sells the products of the games' stores, checking again what the game checks, and more."""

    def __init__(
        self,
        products: ProductStore,
        store: PurchaseStore,
        catalogues: Catalogues,
        heroes: Heroes,
        entitlements: EntitlementStore,
    ) -> None:
        """Sell the products a store keeps, paid and given in another.

        Args:
            products: Where the products are kept.
            store: Where purchases are paid, given and recorded.
            catalogues: The games' imported catalogues, which tell what fits a hero.
            heroes: The heroes who buy, with their factions, classes and levels.
            entitlements: Where the items the heroes hold are kept.
        """
        self._products = products
        self._store = store
        self._catalogues = catalogues
        self._heroes = heroes
        self._entitlements = entitlements

    async def purchase(self, game: Game, persona: Persona, product_id: str) -> None:
        """Sell a hero a product of its game's store, for the product's price.

        The checks the game makes before it asks are made again, as a request may come from
        elsewhere, and more: the item must fit the hero's faction, class and level, an
        ability's level needs the level below it, and an item owned for good is not sold again.

        Args:
            game: The hero's game.
            persona: The hero, which the asking account owns.
            product_id: The id the game buys the product by.

        Raises:
            PurchaseRefused: When the store does not sell the product, the hero may not have
                the item or owns it for good, or the price is more than the wallet holds.
        """
        rules = game.store
        number = rules.number(product_id) if rules else None
        product = await self._products.product(game.id, number) if number is not None else None
        hero = next(
            (
                hero
                for hero in (await self._heroes.roster(game, persona.account_id)).heroes
                if hero.persona.id == persona.id
            ),
            None,
        )
        if product is None and number is not None:
            listed = await self._products.bundle(game.id, number)
            if listed is None:
                raise PurchaseRefused(UNKNOWN_PRODUCT)
            bundle = listed.bundle
            if (
                hero is None
                or bundle.faction not in (None, hero.faction)
                or bundle.hero_class not in (None, hero.hero_class)
            ):
                raise PurchaseRefused(NOT_FOR_HERO)
            loadout = granted(game.heroes, hero.faction, hero.hero_class)
            items = tuple(
                (member.item_id, member.ownership)
                for member in listed.members
                if gains(member.item_id, member.ownership, loadout)
            )
            wallet = wallet_of(game, persona, bundle.currency)
            await self._store.buy(
                Purchase(game.id, persona, product_id, "bundle", wallet, bundle.price, items)
            )
            return
        if product is None:
            raise PurchaseRefused(UNKNOWN_PRODUCT)
        item = self._catalogues.contents(game).items.get(product.item_id)
        if (
            item is None
            or hero is None
            or not fits(item, {item.kind}, hero.faction, hero.hero_class, hero.level)
        ):
            raise PurchaseRefused(NOT_FOR_HERO)
        level = item.levels.index(item.id) if item.id in item.levels else 0
        if level and game.heroes:
            kept = await self._entitlements.of_persona(persona.id)
            if item.levels[level - 1] not in held(game.heroes, hero.faction, hero.hero_class, kept):
                raise PurchaseRefused(LEVEL_MISSING)
        loadout = granted(game.heroes, hero.faction, hero.hero_class)
        if not gains(item.id, product.ownership, loadout):
            raise PurchaseRefused(ALREADY_OWNED)
        wallet = wallet_of(game, persona, product.currency)
        bought = ((product.item_id, product.ownership),)
        await self._store.buy(
            Purchase(game.id, persona, product_id, "product", wallet, product.price, bought)
        )
