from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from openfesl.core.admins import Admin
from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.games import Game
from openfesl.core.hero_items import granted
from openfesl.core.inventory import COUNTED, RENTED, Ownership
from openfesl.core.store import Product
from openfesl.core.store_rules import StoreRules
from openfesl.core.wallets import MOST_BALANCE


class Unsold(StrEnum):
    """Why a game's store never sells an item, whatever its price points say.

    Withheld by the game's data, held by every hero it fits, in a starting kit that the game
    grants itself, part of a hero's look from its creation, left out by the game's data, or
    priced by no rule.
    """

    WITHHELD = "withheld"
    BASE = "base"
    STARTING = "starting"
    LOOK = "look"
    EXCLUDED = "excluded"
    UNPRICED = "unpriced"


class PriceState(StrEnum):
    """How a game's store sells an item.

    On sale at its rule's prices, not sold, on sale at prices of its own, or at the fixed prices
    of the heroes' points, which the game's menu takes.
    """

    SALE = "sale"
    OFF = "off"
    OWN = "own"
    FIXED = "fixed"


class PriceRefusal(StrEnum):
    """Why the admin's price points of an item are not saved, none of them.

    A price below 1 or above what a balance holds, a currency and ownership the item has a price
    point of already, a rental of days the rules know no share of, or uses that are no number of
    them.
    """

    PRICE = "price"
    TWICE = "twice"
    DURATION = "duration"
    USES = "uses"


class PriceRefused(Exception):
    """Raised for price points of an item that are not saved."""

    def __init__(self, refusal: PriceRefusal, product: int | None = None) -> None:
        """Say why, and of which price point.

        Args:
            refusal: Why.
            product: The number of the price point's product; none for one to add.
        """
        super().__init__(refusal.value)
        self.refusal = refusal
        self.product = product


@dataclass(frozen=True, slots=True)
class PriceLine:
    """One price point of an item: a product of the store beside what the item's rule makes it.

    Attributes:
        product: The product.
        rule: What the rule of the item's kind prices it at; none where no rule prices it so.
    """

    product: Product
    rule: int | None

    @property
    def own(self) -> bool:
        """Whether the price is the item's own rather than its rule's."""
        return self.product.price != self.rule


@dataclass(frozen=True, slots=True)
class ItemPrices:
    """An item of a game's catalogue with the price points its store keeps of it.

    Attributes:
        item: The item; an ability's first level for all of its levels.
        lines: Its price points, of each of an ability's levels in turn.
        unsold: Why the store never sells it, if it never does.
        fixed: Whether it is priced in the heroes' points alone, which no admin changes.
        alone: Whether its rules sell it alone, rather than let only boxes and sets give it.
    """

    item: Item
    lines: tuple[PriceLine, ...]
    unsold: Unsold | None
    fixed: bool
    alone: bool

    @property
    def state(self) -> PriceState:
        """How the store sells it."""
        if self.fixed:
            return PriceState.FIXED
        if self.unsold or not any(line.product.published for line in self.lines):
            return PriceState.OFF
        return PriceState.OWN if any(line.own for line in self.lines) else PriceState.SALE


@dataclass(frozen=True, slots=True)
class PointChange:
    """A price point of an item as the admin saves it.

    Attributes:
        product: The number of its product; none for a price point to add.
        item_id: The item's number.
        ownership: How a hero owns the item once bought.
        currency: The currency of the price.
        price: The price.
        published: Whether the store sells it.
    """

    product: int | None
    item_id: int
    ownership: Ownership
    currency: str
    price: int
    published: bool


class PriceStore(Protocol):
    """Where the price points of the games' stores are kept and the admin changes them."""

    async def every(self, game: str) -> list[Product]:
        """Return every product of a game's store for heroes, published or not, in order.

        Args:
            game: The game's data-file name.
        """

    async def save_points(
        self, game: str, item_id: int, points: Sequence[PointChange], reset: bool, admin: Admin
    ) -> bool:
        """Save an item's price points and record what changed among the admin's actions.

        Args:
            game: The game's data-file name.
            item_id: The item's number, an ability's first level for all of its levels.
            points: Its price points, those to add among them.
            reset: Whether they are its rule's, as the admin reset them.
            admin: The admin who saved them.

        Returns:
            Whether anything changed.

        Raises:
            PriceRefused: When a point to add is one the store has, also at the same time.
        """

    async def apply_rule(
        self,
        game: str,
        settings: Mapping[str, str | None],
        points: Sequence[PointChange],
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Change a price rule and the price points it gives, and record it as one action.

        Args:
            game: The game's data-file name.
            settings: The settings that override the rule's fields, by key; None removes one.
            points: The price points the rule changes, those to add among them.
            details: What the admin's log keeps of the change: the rule and what it changed.
            admin: The admin who applied it.
        """

    async def save_account(
        self,
        game: str,
        settings: Mapping[str, str | None],
        key: tuple[int, int] | None,
        changes: Sequence[dict[str, Any]],
        admin: Admin,
    ) -> None:
        """Change account prices, allowances and per-round caps, recording each as an action.

        Args:
            game: The game's data-file name.
            settings: The settings that override them, by key; None removes one.
            key: The item number of the chest key and its new price, which the key's product
                takes too; none when the price stays.
            changes: What the admin's log keeps of each changed value.
            admin: The admin who changed them.
        """


@dataclass(frozen=True, slots=True)
class Applied:
    """What applying a changed price rule does to the items it prices.

    Attributes:
        points: The price points it changes, those it adds among them.
        changed: How many items take its prices, having none of their own in its currency.
        kept: How many items keep their own prices or ways of being sold.
        example: The first item that takes its prices, with its points then.
    """

    points: list[PointChange]
    changed: int
    kept: int
    example: tuple[Item, list[PointChange]] | None


def applied(old: StoreRules, new: StoreRules, key: str, found: Iterable[ItemPrices]) -> Applied:
    """Return what applying a changed price rule does to the items it prices.

    An item whose points in the rule's currency all hold the old rule's prices and are sold as it
    sells them takes the new rule's: its prices, sold as it sells them, its other points in the
    currency switched off and the ones it sells added. An item with a price of its own, or a point
    sold otherwise, such as one not sold at all, keeps its points, as do the items the store never
    sells.

    Args:
        old: The rules before the change, by which the items' prices are their own or not.
        new: The rules after it.
        key: The changed rule's key.
        found: The items of the game's catalogue with their price points.
    """
    before, after = old.prices[key], new.prices[key]
    points: list[PointChange] = []
    changed = kept = 0
    example = None
    for prices in found:
        item = prices.item
        if prices.unsold or not any(rule is before for rule in old.rules(item)):
            continue
        lines = [line for line in prices.lines if line.product.currency == before.currency]
        if any(
            line.own or line.product.published != (line.product.ownership in before.sold)
            for line in lines
        ):
            kept += 1
            continue
        taken = []
        for line in lines:
            product = line.product
            price = new.price(after, product.ownership)
            taken.append(
                PointChange(
                    product.id,
                    product.item_id,
                    product.ownership,
                    product.currency,
                    price or product.price,
                    price is not None and product.ownership in after.sold,
                )
            )
        held = {line.product.ownership for line in lines}
        for ownership in after.sold:
            if ownership not in held and (price := new.price(after, ownership)):
                taken.append(PointChange(None, item.id, ownership, after.currency, price, True))
        changed += 1
        points += taken
        example = example or (item, taken)
    return Applied(points, changed, kept, example)


def item_prices(
    game: Game, rules: StoreRules, catalogue: Catalogue, products: Iterable[Product]
) -> list[ItemPrices]:
    """Return the items of the kinds a game's store prices, each with its price points.

    Items without a name, leftovers of the game's making, are left out, as are an ability's
    later levels, which its first one stands for.

    Args:
        game: The game.
        rules: How its store prices items, with the instance's settings over its data.
        catalogue: Its imported catalogue.
        products: The products its store sells heroes, published or not.
    """
    held: dict[int, list[Product]] = {}
    for product in products:
        held.setdefault(product.item_id, []).append(product)
    kinds = {rule.kind for rule in rules.prices.values()}
    heroes = game.heroes
    starting = (
        {
            number
            for faction in heroes.factions
            for hero_class in heroes.classes
            for number in granted(heroes, faction, hero_class)
        }
        if heroes
        else set()
    )
    points = heroes.points if heroes else None
    found = []
    for item in sorted(catalogue.contents.items.values(), key=lambda item: item.id):
        if item.kind not in kinds or not catalogue.name(item.id):
            continue
        if item.levels and item.id != item.levels[0]:
            continue
        priced = rules.rules(item)
        lines = tuple(
            PriceLine(product, rules.price_of(level, product.ownership, product.currency))
            for number in item.levels or (item.id,)
            if (level := catalogue.item(number))
            for product in held.get(number, [])
        )
        fixed = bool(priced) and all(rule.currency == points for rule in priced)
        unsold = _unsold(rules, item, starting, bool(priced))
        found.append(ItemPrices(item, lines, unsold, fixed, rules.sells_alone(item)))
    return found


def checked_price(price: int, product: int | None = None) -> int:
    """Return a price the admin set, if a balance can pay it.

    Args:
        price: The price.
        product: The number of the price point's product, which a refusal names.

    Raises:
        PriceRefused: When it is below 1 or above what a balance holds.
    """
    if not 0 < price <= MOST_BALANCE:
        raise PriceRefused(PriceRefusal.PRICE, product)
    return price


def new_point(
    rules: StoreRules, prices: ItemPrices, currency: str, ownership: Ownership, price: int | None
) -> PointChange:
    """Return a price point the admin adds to an item, sold at once.

    Args:
        rules: How the game's store prices items.
        prices: The item with its price points.
        currency: The price's currency.
        ownership: How a hero owns the item once bought.
        price: The price; the rule's when none.

    Raises:
        PriceRefused: When the rules know no such rental, the uses are no number of them, the item
            has the point already, or the price is none that a balance can pay.
    """
    if ownership.type == RENTED and ownership.days not in rules.rentals:
        raise PriceRefused(PriceRefusal.DURATION)
    if ownership.type == COUNTED and not 0 < (ownership.uses or 0) <= MOST_BALANCE:
        raise PriceRefused(PriceRefusal.USES)
    if (currency, ownership) in {
        (line.product.currency, line.product.ownership) for line in prices.lines
    }:
        raise PriceRefused(PriceRefusal.TWICE)
    if price is None:
        price = rules.price_of(prices.item, ownership, currency) or 0
    return PointChange(None, prices.item.id, ownership, currency, checked_price(price), True)


def reset_points(rules: StoreRules, prices: ItemPrices) -> list[PointChange]:
    """Return an item's price points as the rules of its kind make them.

    Each point a rule prices takes the rule's price and is sold as the rule sells it, the others
    are switched off, and the points that a rule sells and the item lacks are added.

    Args:
        rules: How the game's store prices items.
        prices: The item with its price points.
    """
    item = prices.item
    sold = {(rule.currency, ownership) for rule in rules.rules(item) for ownership in rule.sold}
    points = []
    for line in prices.lines:
        product = line.product
        key = (product.currency, product.ownership)
        points.append(
            PointChange(
                product.id,
                product.item_id,
                product.ownership,
                product.currency,
                line.rule or product.price,
                line.rule is not None and key in sold,
            )
        )
    held = {(line.product.currency, line.product.ownership) for line in prices.lines}
    for currency, ownership in sorted(sold - held, key=lambda key: (key[0], key[1].written)):
        if price := rules.price_of(item, ownership, currency):
            points.append(PointChange(None, item.id, ownership, currency, price, True))
    return points


def _unsold(rules: StoreRules, item: Item, starting: set[int], priced: bool) -> Unsold | None:
    if item.withheld:
        return Unsold.WITHHELD
    if item.base:
        return Unsold.BASE
    if item.id in starting:
        return Unsold.STARTING
    if item.slot in rules.excluded.slots:
        return Unsold.LOOK
    if item.id in rules.excluded.items:
        return Unsold.EXCLUDED
    return None if priced else Unsold.UNPRICED
