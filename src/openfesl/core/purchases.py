from dataclasses import dataclass
from typing import Protocol, TypeGuard

from openfesl.core.catalogues import Catalogues, Item
from openfesl.core.drops import Drops
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game
from openfesl.core.hero_items import fits, granted, held
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.inventory import Ownership, gains
from openfesl.core.magma import MagmaError
from openfesl.core.personas import Persona
from openfesl.core.store import Bundle, Offer, Product, ProductStore, offer
from openfesl.core.texts import BASE_LANGUAGE
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
        kind: What was bought: a product, a bundle or a box.
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


@dataclass(frozen=True, slots=True)
class Refund:
    """What a refund of a hero's abilities gave back and took.

    Attributes:
        currency: The currency the abilities were paid in.
        balance: The hero's balance in it after the refund.
        items: The items taken back, by number.
    """

    currency: str
    balance: int
    items: list[int]


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

    async def buy_for_account(
        self, account_id: int, product_id: str, product: Product, wallet: Wallet
    ) -> None:
        """Pay a product's price, give the account the item and record it, all or nothing.

        Args:
            account_id: The account.
            product_id: The id the product was bought by.
            product: The product, which the account owns once bought.
            wallet: The account's wallet the price is paid from.

        Raises:
            PurchaseRefused: When the wallet holds less than the price, also after another
                purchase at the same time.
        """

    async def refund(self, persona: Persona, wallet: Wallet) -> Refund | None:
        """Take back what a hero bought from a wallet and credit what it paid, all or nothing.

        Args:
            persona: The hero.
            wallet: The hero's own wallet, whose purchases are refunded.

        Returns:
            What was refunded, or None when nothing bought from the wallet is left to refund.
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
        drops: Drops,
    ) -> None:
        """Sell the products a store keeps, paid and given in another.

        Args:
            products: Where the products are kept.
            store: Where purchases are paid, given and recorded.
            catalogues: The games' imported catalogues, which tell what fits a hero.
            heroes: The heroes who buy, with their factions, classes and levels.
            entitlements: Where the items the heroes hold are kept.
            drops: Opens the boxes the heroes buy.
        """
        self._products = products
        self._store = store
        self._catalogues = catalogues
        self._heroes = heroes
        self._entitlements = entitlements
        self._drops = drops

    async def purchase(self, game: Game, persona: Persona, product_id: str) -> Offer | None:
        """Sell a hero a product, a bundle or a box of its game's store, for its price.

        The checks the game makes before it asks are made again, as a request may come from
        elsewhere, and more: the item must fit the hero's faction, class and level, an
        ability's level needs the level below it, and an item owned for good is not sold again.

        Args:
            game: The hero's game.
            persona: The hero, which the asking account owns.
            product_id: The id the game buys the product by.

        Returns:
            The product a box gave the hero; none for a product or a bundle.

        Raises:
            PurchaseRefused: When the store does not sell the product, the hero may not have
                the item or owns it for good, or the price is more than the wallet holds.
        """
        rules = game.store
        number = rules.number(product_id) if rules else None
        product = await self._products.product(game.id, number) if number is not None else None
        hero = await self._hero(game, persona)
        if product is None and number is not None:
            listed = await self._products.bundle(game.id, number)
            if listed is None:
                raise PurchaseRefused(UNKNOWN_PRODUCT)
            bundle = listed.bundle
            if not _for(hero, bundle.faction, bundle.hero_class):
                raise PurchaseRefused(NOT_FOR_HERO)
            if bundle.box is not None:
                return await self._open_box(game, persona, product_id, bundle, hero)
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
            return None
        if product is None:
            raise PurchaseRefused(UNKNOWN_PRODUCT)
        item = self._catalogues.contents(game).items.get(product.item_id)
        if hero is None or not _fitting(item, hero):
            raise PurchaseRefused(NOT_FOR_HERO)
        assert item is not None
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
        return None

    async def _open_box(
        self, game: Game, persona: Persona, product_id: str, bundle: Bundle, hero: Hero | None
    ) -> Offer:
        box = await self._products.box(game.id, bundle.box) if bundle.box is not None else None
        if box is None or hero is None or not _for(hero, box.faction, box.hero_class):
            raise PurchaseRefused(NOT_FOR_HERO)
        won = await self._drops.roll(game, box, persona, hero)
        if won is None:
            raise PurchaseRefused(ALREADY_OWNED)
        wallet = wallet_of(game, persona, bundle.currency)
        items = ((won.item_id, won.ownership),)
        await self._store.buy(
            Purchase(game.id, persona, product_id, "box", wallet, bundle.price, items)
        )
        return offer(game, await self._catalogues.of(game, BASE_LANGUAGE), won)

    async def _hero(self, game: Game, persona: Persona) -> Hero | None:
        roster = await self._heroes.roster(game, persona.account_id)
        return next((hero for hero in roster.heroes if hero.persona.id == persona.id), None)

    async def purchase_for_account(self, game: Game, account_id: int, product_id: str) -> None:
        """Sell an account a product that it owns rather than one of its heroes, such as a key.

        Args:
            game: The game.
            account_id: The account.
            product_id: The id the product is bought by.

        Raises:
            PurchaseRefused: When the store sells no such product to accounts, or the price is
                more than the account's wallet holds.
        """
        rules = game.store
        number = rules.number(product_id) if rules else None
        product = (
            await self._products.product(game.id, number, account=True)
            if number is not None
            else None
        )
        if product is None:
            raise PurchaseRefused(UNKNOWN_PRODUCT)
        allowance = game.currencies[product.currency].allowance
        wallet = Wallet(game.id, product.currency, account_id, None, allowance)
        await self._store.buy_for_account(account_id, product_id, product, wallet)

    async def refund(self, game: Game, persona: Persona) -> Refund | None:
        """Refund a hero's abilities: take back all it bought with its points and credit them.

        Only what was bought is taken back, so the abilities the hero starts with stay.

        Args:
            game: The hero's game.
            persona: The hero, which the asking account owns.

        Returns:
            What was refunded, or None when the hero bought nothing with its points.
        """
        if game.heroes is None:
            return None
        return await self._store.refund(persona, wallet_of(game, persona, game.heroes.points))


def _for(hero: Hero | None, faction: str | None, hero_class: str | None) -> TypeGuard[Hero]:
    # A hero fits what names no faction or class, or names its own.
    return (
        hero is not None
        and faction in (None, hero.faction)
        and hero_class in (None, hero.hero_class)
    )


def _fitting(item: Item | None, hero: Hero) -> bool:
    return item is not None and fits(item, {item.kind}, hero.faction, hero.hero_class, hero.level)
