from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from openfesl.core.catalogues import Catalogue, Catalogues, Item, paragraphs
from openfesl.core.deals import HotDeals
from openfesl.core.drops import Box
from openfesl.core.entitlements import ACTIVE, Entitlement, EntitlementStore
from openfesl.core.games import Game, Scope
from openfesl.core.hero_items import held, holding
from openfesl.core.heroes import Hero
from openfesl.core.inventory import PERMANENT, Held, Ownership
from openfesl.core.store import ListedBundle, ProductStore, price_order
from openfesl.core.wallets import Wallets


class Kind(StrEnum):
    """What a page of a game's store sells: an item alone, a hot deal's bundle or a random box."""

    ITEM = "item"
    DEAL = "deal"
    BOX = "box"


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
        number: The number of the product or bundle that is bought.
        ownership: How the hero owns the item; none for a bundle or a box.
        currency: The currency of the price.
        price: The price.
        original_price: The price before a sale, while it is on sale.
    """

    number: int
    ownership: Ownership | None
    currency: str
    price: int
    original_price: int | None = None


@dataclass(frozen=True, slots=True)
class Content:
    """An item that a bundle holds or a box may give.

    Attributes:
        name: Its name.
        ownership: How the hero owns it.
        chance: How likely a box gives it, from 0 to 1; none for a bundle's item.
    """

    name: str
    ownership: Ownership
    chance: float | None = None


@dataclass(frozen=True, slots=True)
class StoreItem:
    """What one page of a game's store sells, named in a language.

    Attributes:
        number: The number the page is found by.
        kind: An item alone, a hot deal or a random box.
        name: Its name.
        description: What the game says about it, in paragraphs: an item's own text, or a
            hot deal's description; none for a box.
        item: The item, for an item alone.
        requirement: Which heroes may have it.
        prices: The ways of buying it.
        contents: The items a bundle holds or a box may give.
        ends: When a hot deal ends, if it does.
        ended: Whether a hot deal ended.
        worth: What every item a box gives is worth at least, if the box promises it.
        sold_alone: Whether the store also sells each of a bundle's items alone.
    """

    number: int
    kind: Kind
    name: str
    description: tuple[str, ...]
    item: Item | None
    requirement: Requirement
    prices: tuple[PricePoint, ...]
    contents: tuple[Content, ...] = ()
    ends: datetime | None = None
    ended: bool = False
    worth: int | None = None
    sold_alone: bool = False


@dataclass(frozen=True, slots=True)
class HeroChoice:
    """One of an account's heroes as a store page offers it what it sells.

    Attributes:
        hero: The hero.
        unfit: Why it may not have it; none if it may.
        held: How it holds the item sold alone, if it does; for good when the game grants it
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
        deals: HotDeals,
        catalogues: Catalogues,
        entitlements: EntitlementStore,
        wallets: Wallets,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Read the stores' products and bundles, and the heroes' items and wallets.

        Args:
            products: Where the products, bundles and boxes are kept.
            deals: The hot deals, which list the bundles sold now.
            catalogues: The games' imported catalogues, which name the items and say who fits.
            entitlements: Where the items the heroes hold are kept.
            wallets: The balances the heroes pay with.
            clock: Tells the time; the system's own when none.
        """
        self._products = products
        self._deals = deals
        self._catalogues = catalogues
        self._entitlements = entitlements
        self._wallets = wallets
        self._clock = clock or (lambda: datetime.now(UTC))

    async def find(self, game: Game, number: int, language: str) -> StoreItem | None:
        """Return what a game's store sells by a number, named in a language.

        A product's number finds its item with every product the item is sold alone by, and a
        bundle's number its hot deal or random box, also once the deal has ended.

        Args:
            game: The game, which has a store and heroes.
            number: The number of a product or a bundle.
            language: The language code; English where the texts lack it.

        Returns:
            What the page sells; none for a number the store sells nothing by to heroes, or a
            bundle that is not sold now and has not ended.
        """
        if game.store is None or game.heroes is None:
            return None
        catalogue = await self._catalogues.of(game, language)
        product = await self._products.product(game.id, number)
        if product is not None:
            item = catalogue.item(product.item_id)
            if item is None:
                return None
            sold = await self._products.of_item(game.id, product.item_id)
            sold.sort(key=lambda found: price_order(list(game.currencies), found))
            return StoreItem(
                number=number,
                kind=Kind.ITEM,
                name=catalogue.name(item.id) or str(item.id),
                description=game.store.describe(catalogue, item.id),
                item=item,
                requirement=requirement(game, item),
                prices=tuple(
                    PricePoint(found.id, found.ownership, found.currency, found.price)
                    for found in sold
                ),
            )
        listed = await self._deals.bundle(game, number)
        ended = False
        if listed is None:
            listed = await self._products.bundle(game.id, number)
            ends = listed.bundle.ends if listed else None
            if ends is None or ends > self._clock():
                return None
            ended = True
        assert listed is not None
        return await self._bundle(game, catalogue, listed, ended, language)

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
            if unfit is None and item and item.id in item.levels[1:]:
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

    async def _bundle(
        self, game: Game, catalogue: Catalogue, listed: ListedBundle, ended: bool, language: str
    ) -> StoreItem:
        bundle = listed.bundle
        deal = await self._deals.deal(game, listed, language)
        box = await self._products.box(game.id, bundle.box) if bundle.box is not None else None
        requirement = Requirement(
            frozenset({bundle.faction} if bundle.faction else ()),
            frozenset({bundle.hero_class} if bundle.hero_class else ()),
        )
        price = PricePoint(listed.id, None, bundle.currency, bundle.price, bundle.original_price)
        if box is None:
            contents = tuple(
                Content(member.name, member.product.ownership) for member in deal.members
            )
            sold_alone = all(member.product.published for member in deal.members)
        else:
            contents = _drops(catalogue, box)
            sold_alone = False
        return StoreItem(
            number=listed.id,
            kind=Kind.DEAL if box is None else Kind.BOX,
            name=deal.name,
            # A bundle without a description of its own is described by its items' names, which
            # the page lists anyway.
            description=paragraphs(deal.description) if bundle.description and box is None else (),
            item=None,
            requirement=requirement,
            prices=(price,),
            contents=contents,
            ends=bundle.ends,
            ended=ended,
            worth=box.value if box and box.worth_rule else None,
            sold_alone=sold_alone,
        )


def requirement(game: Game, item: Item) -> Requirement:
    """Return which heroes may have an item; one that fits every faction or class asks for none.

    Args:
        game: The game, which has heroes.
        item: The item, from the game's catalogue.
    """
    rules = game.heroes
    assert rules is not None
    factions = item.factions if not set(rules.factions) <= item.factions else frozenset()
    classes = item.classes if not set(rules.classes) <= item.classes else frozenset()
    return Requirement(factions, classes, item.level or 1)


def _held(item: Item | None, holds: set[int], kept: list[Entitlement]) -> Held | None:
    # How a hero holds an item: as its entitlement in use says, or for good when the game
    # grants it the item itself, as its faction and class's loadout or a base item.
    if item is None or (item.id not in holds and not item.base):
        return None
    return holding(item.id, kept)


def _drops(catalogue: Catalogue, box: Box) -> tuple[Content, ...]:
    # Each tier is as likely as its weight against the others', and its drops alike.
    tiers: dict[int, tuple[int, int]] = {}
    for drop in box.drops:
        weight, count = tiers.get(drop.tier, (drop.weight, 0))
        tiers[drop.tier] = (weight, count + 1)
    total = sum(weight for weight, _ in tiers.values())
    return tuple(
        Content(
            catalogue.name(drop.product.item_id) or str(drop.product.item_id),
            drop.product.ownership,
            tiers[drop.tier][0] / total / tiers[drop.tier][1],
        )
        for drop in box.drops
    )
