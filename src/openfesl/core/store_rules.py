from typing import TYPE_CHECKING, Annotated, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership

# For annotations only, since the catalogue's module reads the games' data, which holds these.
if TYPE_CHECKING:
    from openfesl.core.catalogues import Item

type Written = Annotated[Ownership, BeforeValidator(Ownership.read)]


class PriceRule(BaseModel):
    """What items of a kind are worth in one currency, and how the store sells them.

    Attributes:
        kind: The kind of the items, as the catalogue knows it.
        items: The numbers of the items it covers; every item of the kind when empty.
        fields: Values that the items' definitions hold, by field, compared ignoring capitals,
            with a field the definition lacks read as empty.
        currency: The currency.
        value: What owning an item for good is worth, of which a rental costs its share.
        use_price: What one use of an item limited by uses costs.
        sold: How the store sells the items, written perm, as a rental such as 30d or as uses
            such as x25; none for items it only prices, such as those that boxes drop.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    items: frozenset[int] = frozenset()
    fields: dict[str, str] = {}
    currency: str
    value: PositiveInt | None = None
    use_price: PositiveInt | None = None
    sold: tuple[Written, ...] = ()

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


class StoreRules(BaseModel):
    """How a game's store prices and sells the items of its catalogue, as its data seeds it.

    The admin changes the products from there; the rules only fill a store that has none.

    Attributes:
        ids: The form of the ids that the game buys products by, with {id} for the product's
            number.
        rentals: The share of an item's value that a rental costs in percent, by its days.
        rounding: How a rental's price is rounded down, by currency; kept exact in others.
        prices: The price rules; the first rule of each currency that covers an item prices it
            in that currency.
        excluded: The items never sold nor dropped.
    """

    model_config = ConfigDict(frozen=True)

    ids: Annotated[str, Field(pattern=r"^[^/?#% ]*\{id\}[^/?#% ]*$")]
    rentals: dict[PositiveInt, Annotated[int, Field(gt=0, le=100)]] = {}
    rounding: dict[str, Rounding] = {}
    prices: list[PriceRule]
    excluded: Exclusions = Exclusions()

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

    def rules(self, item: Item) -> list[PriceRule]:
        """Return the rule that prices an item in each currency, in the rules' order.

        Args:
            item: The item, from the game's catalogue.
        """
        found: dict[str, PriceRule] = {}
        for rule in self.prices:
            if rule.currency not in found and rule.covers(item):
                found[rule.currency] = rule
        return list(found.values())

    def price(self, rule: PriceRule, ownership: Ownership) -> int | None:
        """Return what owning an item a rule covers in a way costs, if the rule prices that way.

        Args:
            rule: The rule.
            ownership: How the item is owned.
        """
        days, uses = ownership.days, ownership.uses
        if ownership.type == PERMANENT:
            return rule.value
        if ownership.type == RENTED and rule.value and days in self.rentals:
            price = rule.value * self.rentals[days] // 100
            rounding = self.rounding.get(rule.currency)
            return rounding.rounded(price) if rounding else price
        if ownership.type == COUNTED and rule.use_price and uses:
            return rule.use_price * uses
        return None

    @model_validator(mode="after")
    def _check_sold(self) -> Self:
        for rule in self.prices:
            for ownership in rule.sold:
                if not self.price(rule, ownership):
                    raise ValueError(f"a rule of {rule.kind} sells {ownership} at no price")
        return self
