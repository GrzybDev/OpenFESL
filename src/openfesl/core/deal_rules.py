from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt

from openfesl.core.calendar import Schedule, Zone
from openfesl.core.store_rules import Rounding

type Percent = Annotated[int, Field(ge=0, lt=100)]


class ItemSection(BaseModel):
    """A section of hot deals that puts a few items of one faction together, at a discount.

    Attributes:
        kind: items.
        enabled: Whether the section is listed.
        schedule: When its deals change.
        size: How many deals each faction gets.
        pieces: How many items a deal holds, each worn in another slot.
        slots: The slots its items are worn in.
        discount: The discount on the items' prices, in percent.
        name: The id of the text that names a deal, with {faction} for the faction's name.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["items"]
    enabled: bool = True
    schedule: Schedule
    size: NonNegativeInt = 1
    pieces: PositiveInt
    slots: frozenset[str]
    discount: Percent
    name: str


class DealRules(BaseModel):
    """How a game's hot deals change by themselves, beside the offers the admin adds.

    Attributes:
        enabled: Whether the rotation runs; without it only the admin's offers are listed.
        zone: The time zone at whose midnight the sections change.
        currency: The currency of the deals' prices.
        price_ending: What the last two digits of a price worked out from its items are, as
            it is rounded down to them; such prices are kept exact when none.
        sections: The sections in the order the hot deals list them, by name.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    zone: Zone = "UTC"
    currency: str
    price_ending: Annotated[int, Field(ge=0, lt=100)] | None = None
    sections: dict[str, ItemSection]

    def rounded(self, price: int) -> int:
        """Return a price worked out from items' prices as the deals end it.

        Args:
            price: The price.
        """
        if self.price_ending is None:
            return price
        return Rounding(step=100, ending=self.price_ending).rounded(price)
