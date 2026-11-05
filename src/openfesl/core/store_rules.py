from functools import cached_property
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from openfesl.core.game_pages import Glyph
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership

# For annotations only, since the catalogue's module reads the games' data, which holds these.
if TYPE_CHECKING:
    from openfesl.core.catalogues import Catalogue, Item

type Written = Annotated[Ownership, BeforeValidator(Ownership.read)]


def _read_drop(text: object) -> object:
    # A drop as game data writes it: an item's number and its ownership, such as 2153 x75, or
    # the number alone for an item owned for good.
    if isinstance(text, int) and not isinstance(text, bool):
        return text, Ownership(PERMANENT)
    if not isinstance(text, str):
        return text
    item, _, written = text.partition(" ")
    if not (item.isascii() and item.isdecimal()):
        raise ValueError(f"{text!r} is no drop such as 2153 x75")
    return int(item), Ownership.read(written)


type DropEntry = Annotated[tuple[int, Ownership], BeforeValidator(_read_drop)]


class PriceRule(BaseModel):
    """What items of a kind are worth in one currency, and how the store sells them.

    Attributes:
        kind: The kind of the items, as the catalogue knows it; none for a listing, which names
            its items.
        items: The numbers of the items it covers; every item of the kind when empty.
        fields: Values that the items' definitions hold, by field, compared ignoring capitals,
            with a field the definition lacks read as empty.
        currency: The currency.
        value: What owning an item for good is worth, of which a rental costs its share.
        use_price: What one use of an item limited by uses costs.
        points: What the ways of owning the items cost, as the store last listed them, by the
            way, written as in sold; the rule sells them unless it says otherwise.
        sold: How the store sells the items, written perm, as a rental such as 30d or as uses
            such as x25; none for items it only prices, such as those that boxes drop.
    """

    model_config = ConfigDict(frozen=True)

    kind: str = ""
    items: frozenset[int] = frozenset()
    fields: dict[str, str] = {}
    currency: str
    value: PositiveInt | None = None
    use_price: PositiveInt | None = None
    points: dict[str, PositiveInt] = {}
    sold: tuple[Written, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _sell_points(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {"sold": list(data.get("points", ())), **data}

    def covers(self, item: Item) -> bool:
        """Tell whether the rule prices an item.

        Args:
            item: The item, from the game's catalogue.
        """
        return (
            item.kind == self.kind
            and (not self.items or item.id in self.items)
            and all(
                item.fields.get(field, "").strip().lower() == value.lower()
                for field, value in self.fields.items()
            )
        )


class Exclusions(BaseModel):
    """The items of a game's catalogue that its store never sells nor drops.

    Attributes:
        items: The items, by number.
        slots: The slots whose items are excluded, such as those a hero's look takes from its
            creation.
    """

    model_config = ConfigDict(frozen=True)

    items: frozenset[int] = frozenset()
    slots: frozenset[str] = frozenset()


class Tier(BaseModel):
    """A tier of a box's drops, whose drops are equally likely.

    Attributes:
        weight: How likely the tier is against the box's others.
        drops: The items it gives, each with its ownership, written such as 3025 7d.
        name: The end of the key of its name in the catalogues, for a box that names its tiers.
        rare: Whether its drops are rare, which the Claw marks and lists.
    """

    model_config = ConfigDict(frozen=True)

    weight: PositiveInt
    drops: tuple[DropEntry, ...]
    name: str = ""
    rare: bool = False


class BoxRule(BaseModel):
    """What a box of a game's store may give, as its data seeds it.

    Attributes:
        key: The name the box is known by.
        kind: box, which hot deals sell, chest, which a key opens, or claw, the Claw's prizes.
        currency: The currency of its value and of the prices its drops are worth.
        value: What each of its drops is worth at least, which is its price.
        worth_rule: Whether it promises that worth; a box that does not may give less.
        faction: The faction whose heroes may open it; any when none.
        hero_class: The class whose heroes may open it; any when none.
        tiers: Its drops, in tiers.
        fallback: What it gives when a hero may get none of its drops.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    kind: Literal["box", "chest", "claw"] = "box"
    currency: str
    value: PositiveInt
    worth_rule: bool = True
    faction: str | None = None
    hero_class: str | None = None
    tiers: tuple[Tier, ...]
    fallback: DropEntry | None = None


class Rounding(BaseModel):
    """How the prices of a currency are rounded down, such as to end in 99.

    Attributes:
        step: What the prices are multiples of, before their ending.
        ending: What each price is above a multiple of the step.
    """

    model_config = ConfigDict(frozen=True)

    step: PositiveInt
    ending: NonNegativeInt = 0

    def rounded(self, price: int) -> int:
        """Return a price rounded down; one too small to round stays as it is.

        Args:
            price: The price.
        """
        rounded = price - (price - self.ending) % self.step
        return rounded if rounded > 0 else price


class StoreCategory(BaseModel):
    """A category of a game's in-game store, which lists the items of some kinds.

    Attributes:
        key: Its name, which its address and the ids of its texts hold.
        kinds: The kinds of its items, as the catalogue knows them.
        glyph: The glyph of its tile.
        equips: Whether its items are equipped to be used, so that buying may equip them.
    """

    model_config = ConfigDict(frozen=True)

    key: Annotated[str, Field(pattern=r"^[a-z][a-z-]*$")]
    kinds: frozenset[str]
    glyph: Glyph
    equips: bool = True


class InGameStore(BaseModel):
    """The store that a game opens in its own browser, where a hero buys during a match.

    Attributes:
        currencies: The currencies it sells for, in the order it lists prices in; products
            priced in others are left out.
        categories: Its categories, in order.
        help: The addresses of the pages that tell how a currency is earned, by currency, as
            the game opens them in the system browser.
    """

    model_config = ConfigDict(frozen=True)

    currencies: list[str]
    categories: list[StoreCategory]
    help: dict[str, str] = {}

    def category(self, key: str) -> StoreCategory | None:
        """Return a category by its name, if the store has one.

        Args:
            key: The category's name.
        """
        return next((category for category in self.categories if category.key == key), None)


class StoreRules(BaseModel):
    """How a game's store prices and sells the items of its catalogue, as its data seeds it.

    The admin changes the products from there; the rules only fill a store that has none.

    Attributes:
        ids: The form of the ids that the game buys products by, with {id} for the product's
            number.
        rentals: The share of an item's value that a rental costs in percent, by its days.
        rounding: How a rental's price is rounded down, by currency; kept exact in others.
        descriptions: The ids of the game's texts that describe an item, with {id} for its
            number; the first one the catalogue holds describes it.
        prices: The price rules by key, in order; the first rule of each currency that covers an
            item prices it in that currency.
        listed: What the store last sold items for, which sells them that way alone; their
            rules still price the other ways of owning them, such as a box's drops.
        excluded: The items never sold nor dropped.
        boxes: The boxes and their drops.
        in_game: The store the game opens in its own browser; none for a game without one.
    """

    model_config = ConfigDict(frozen=True)

    ids: Annotated[str, Field(pattern=r"^[^/?#% ]*\{id\}[^/?#% ]*$")]
    rentals: dict[PositiveInt, Annotated[int, Field(gt=0, le=100)]] = {}
    rounding: dict[str, Rounding] = {}
    descriptions: list[str] = []
    prices: dict[Annotated[str, Field(pattern=r"^[a-z][a-z-]*$")], PriceRule]
    listed: list[PriceRule] = []
    excluded: Exclusions = Exclusions()
    boxes: list[BoxRule] = []
    in_game: InGameStore | None = None

    def product_id(self, number: int) -> str:
        """Return the id that the game buys a product by.

        Args:
            number: The product's number.
        """
        return self.ids.replace("{id}", str(number))

    def number(self, product_id: str) -> int | None:
        """Return the number of a product that the game names by its id, if the id is one.

        Args:
            product_id: The id, as a purchase's path carries it.
        """
        prefix, _, suffix = self.ids.partition("{id}")
        if not (product_id.startswith(prefix) and product_id.endswith(suffix)):
            return None
        number = product_id[len(prefix) : len(product_id) - len(suffix)]
        # A number the database holds, of at most 18 digits.
        if not (number.isascii() and number.isdecimal() and len(number) <= 18):
            return None
        return int(number)

    def sellable(self, item: Item) -> bool:
        """Tell whether the store may sell or drop an item at all.

        Args:
            item: The item, from the game's catalogue.
        """
        return (
            item.id not in self.excluded.items
            and item.slot not in self.excluded.slots
            and not item.base
            and not item.withheld
        )

    def sells_alone(self, item: Item) -> bool:
        """Tell whether the rules sell an item alone, rather than let only boxes and sets give it.

        Args:
            item: The item, from the game's catalogue.
        """
        return any(rule.sold for rule in self.rules(item))

    def counted(self, item: Item) -> bool:
        """Tell whether the store sells an item as uses, so that heroes hold it as uses.

        Args:
            item: The item, from the game's catalogue.
        """
        return any(
            sold.type == COUNTED
            for rule in self.prices.values()
            if rule.covers(item)
            for sold in rule.sold
        )

    def rules(self, item: Item) -> list[PriceRule]:
        """Return what sells an item in each currency: its listings, else the rules of its kind.

        Args:
            item: The item, from the game's catalogue.
        """
        return self._listings.get(item.id) or self._kind_rules(item)

    def price(self, rule: PriceRule, ownership: Ownership) -> int | None:
        """Return what owning an item a rule covers in a way costs, if the rule prices that way.

        Args:
            rule: The rule.
            ownership: How the item is owned.
        """
        days, uses = ownership.days, ownership.uses
        if ownership.written in rule.points:
            return rule.points[ownership.written]
        value = rule.points.get("perm", rule.value)
        if ownership.type == PERMANENT:
            return value
        if ownership.type == RENTED and value and days in self.rentals:
            price = value * self.rentals[days] // 100
            rounding = self.rounding.get(rule.currency)
            return rounding.rounded(price) if rounding else price
        if ownership.type == COUNTED and uses:
            if rule.use_price:
                return rule.use_price * uses
            packs = [(int(way[1:]), price) for way, price in rule.points.items() if way[0] == "x"]
            if packs:
                size, cost = max(packs)
                return cost * uses // size
        return None

    def describe(self, catalogue: Catalogue, item_id: int) -> tuple[str, ...]:
        """Return the game's own text about an item, in paragraphs; none when it has none.

        Args:
            catalogue: The game's catalogue, with its texts in the language shown.
            item_id: The item's number.
        """
        for name in self.descriptions:
            if text := catalogue.text(name.replace("{id}", str(item_id))):
                # The game's texts break lines with |n.
                return tuple(part.strip() for part in text.split("|n") if part.strip())
        return ()

    def price_of(self, item: Item, ownership: Ownership, currency: str) -> int | None:
        """Return what owning an item in a way costs in a currency, if a rule prices it so.

        Args:
            item: The item, from the game's catalogue.
            ownership: How it is owned.
            currency: The currency.
        """
        return next(
            (
                price
                for rule in (*self._listings.get(item.id, ()), *self._kind_rules(item))
                if rule.currency == currency and (price := self.price(rule, ownership))
            ),
            None,
        )

    @cached_property
    def _listings(self) -> dict[int, list[PriceRule]]:
        found: dict[int, list[PriceRule]] = {}
        for rule in self.listed:
            for number in rule.items:
                found.setdefault(number, []).append(rule)
        return found

    def _kind_rules(self, item: Item) -> list[PriceRule]:
        found: dict[str, PriceRule] = {}
        for rule in self.prices.values():
            if rule.currency not in found and rule.covers(item):
                found[rule.currency] = rule
        return list(found.values())

    @model_validator(mode="after")
    def _check_boxes(self) -> Self:
        keys = [box.key for box in self.boxes]
        if len(keys) != len(set(keys)):
            raise ValueError("two boxes have one key")
        return self

    @model_validator(mode="after")
    def _check_sold(self) -> Self:
        for rule in self.prices.values():
            for ownership in rule.sold:
                if not self.price(rule, ownership):
                    raise ValueError(f"a rule of {rule.kind} sells {ownership} at no price")
        return self
