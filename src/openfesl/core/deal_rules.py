from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator

from openfesl.core.calendar import Schedule, Window, Zone
from openfesl.core.store_rules import DropEntry, Rounding

type Percent = Annotated[int, Field(ge=0, lt=100)]


class Offer(BaseModel):
    """What hot deals sell under a name of the game's texts: items together, or a random box.

    Attributes:
        name: The id of its name in the game's texts.
        description: The id of the text the game shows about it; its items' names when none.
        members: Its items, each with its ownership, written such as 1129 for good or
            2153 x25.
        box: The key of the store's box of which it gives one drop, instead of items.
        price: Its price; for items the sum of their prices less the section's discount, for
            a box the box's own, when none.
        pool: The name of the offers it is picked with, such as the other faction's box of a
            family; its own name when none.
        window: The days of each year it is sold on instead of by its section's schedule.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None = None
    members: tuple[DropEntry, ...] = ()
    box: str | None = None
    price: PositiveInt | None = None
    pool: str | None = None
    window: Window | None = None

    @property
    def picked(self) -> str:
        """The name of the offers it is picked with."""
        return self.pool or self.name

    @model_validator(mode="after")
    def _check_contents(self) -> Self:
        if bool(self.members) == bool(self.box):
            raise ValueError(f"the offer {self.name} sells either items or a box")
        return self


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
        shelf: The section of the hot deals page that lists its deals; its own name when none.
        leaves: Whether its deals leave the hot deals for a while rather than end.
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
    shelf: str | None = None
    leaves: bool = False


class OfferSection(BaseModel):
    """A section of hot deals that sells offers: every one, or a few of each faction at a time.

    Attributes:
        kind: offers.
        enabled: Whether the section is listed.
        schedule: When its deals change; none for a section whose offers have windows.
        size: How many offers of each faction a period picks, offers picked together as one
            and those of both factions apart; all of them when none.
        discount: The lowest and highest discount on an offer's items in percent, of which
            each period picks one in steps of 5.
        gap: The days after an offer left the section before it may come back.
        sets: Whether the game's sets are among its offers.
        offers: Its own offers.
        shelf: The section of the hot deals page that lists its deals; its own name when none.
        leaves: Whether its deals leave the hot deals for a while rather than end.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["offers"]
    enabled: bool = True
    schedule: Schedule | None = None
    size: PositiveInt | None = None
    discount: tuple[Percent, Percent] = (0, 0)
    gap: NonNegativeInt = 0
    sets: bool = False
    offers: tuple[Offer, ...] = ()
    shelf: str | None = None
    leaves: bool = False

    @model_validator(mode="after")
    def _check_discount(self) -> Self:
        if self.discount[0] > self.discount[1]:
            raise ValueError("a section's lowest discount is above its highest")
        if self.schedule is None and (self.sets or any(not o.window for o in self.offers)):
            raise ValueError("offers without a window need their section's schedule")
        return self


type DealSection = Annotated[ItemSection | OfferSection, Field(discriminator="kind")]


class DealRules(BaseModel):
    """How a game's hot deals change by themselves, beside the offers the admin adds.

    Attributes:
        enabled: Whether the rotation runs; without it only the admin's offers are listed.
        zone: The time zone at whose midnight the sections change.
        currency: The currency of the deals' prices.
        price_ending: What the last two digits of a price worked out from its items are, as
            it is rounded down to them; such prices are kept exact when none.
        sections: The sections in the order the hot deals list them, by name.
        sets: The sets that sections sell a few of at a time.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    zone: Zone = "UTC"
    currency: str
    price_ending: Annotated[int, Field(ge=0, lt=100)] | None = None
    sections: dict[str, DealSection]
    sets: tuple[Offer, ...] = ()

    def rounded(self, price: int) -> int:
        """Return a price worked out from items' prices as the deals end it.

        Args:
            price: The price.
        """
        if self.price_ending is None:
            return price
        return Rounding(step=100, ending=self.price_ending).rounded(price)

    def offers(self, section: OfferSection) -> tuple[Offer, ...]:
        """Return the offers a section sells from.

        Args:
            section: One of the sections.
        """
        return section.offers + (self.sets if section.sets else ())

    @property
    def texts(self) -> set[str]:
        """The ids of the texts that name and describe the deals, the game's or the server's own."""
        offers, texts = list(self.sets), set()
        for section in self.sections.values():
            if isinstance(section, ItemSection):
                texts.add(section.name)
            else:
                offers += section.offers
        return texts | {
            text for offer in offers for text in (offer.name, offer.description) if text
        }
