from typing import Self

from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt, model_validator

from openfesl.core.calendar import Window, Zone
from openfesl.core.store_rules import DropEntry


class Gift(BaseModel):
    """What a reward gives: items to one hero, or a roll of a box, and Funds and keys.

    Attributes:
        items: The items a hero gets, each with its ownership, written such as 1127 7d.
        box: The key of the store's box of which a hero gets one drop, where the items do not
            fit the hero or there are none.
        funds: The Funds the account gets.
        keys: The treasure chest keys the account gets.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[DropEntry, ...] = ()
    box: str | None = None
    funds: NonNegativeInt = 0
    keys: NonNegativeInt = 0

    @property
    def for_hero(self) -> bool:
        """Whether a hero gets part of the gift, who is chosen when it is claimed."""
        return bool(self.items or self.box)


class Part(BaseModel):
    """What an event gives the heroes of one faction, or the account itself.

    Attributes:
        enabled: Whether it is given.
        faction: The faction whose heroes it is for; the account's own when none.
        name: The id of its name in the game's texts or the server's own; {day} stands for the
            day of a daily event.
        description: The id of the text about it, likewise.
        gift: What it gives.
        days: What it gives instead on some days of a daily event, by the day from 1.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    faction: str | None = None
    name: str
    description: str | None = None
    gift: Gift
    days: dict[PositiveInt, Gift] = {}

    def on(self, day: int | None) -> Gift:
        """Return what the part gives on a day of its event.

        Args:
            day: The day of a daily event from 1; none for an event of one gift.
        """
        return self.days.get(day, self.gift) if day else self.gift


class Event(BaseModel):
    """An event of the yearly rewards calendar.

    Attributes:
        enabled: Whether it runs.
        window: The days of each year it runs on.
        daily: Whether each of its days gives a gift of its own, claimed on that day, rather
            than one gift for all of them.
        parts: Its parts, by name.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    window: Window
    daily: bool = False
    parts: dict[str, Part]


class RewardRules(BaseModel):
    """How a game's site gives rewards that accounts claim, as its data sets them.

    Attributes:
        paused: Whether claims are paused, while everything else goes on.
        zone: The time zone at whose midnight the days start.
        currency: The currency of the Funds that rewards give.
        fallback: What a hero gets for an item of a gift that it owns for good already.
        events: The events of the yearly calendar, by key.
    """

    model_config = ConfigDict(frozen=True)

    paused: bool = False
    zone: Zone = "UTC"
    currency: str
    fallback: DropEntry
    events: dict[str, Event] = {}

    @property
    def gifts(self) -> list[Gift]:
        """Every gift of the rewards: of each event and day."""
        parts = [part for event in self.events.values() for part in event.parts.values()]
        return [gift for part in parts for gift in (part.gift, *part.days.values())]

    @property
    def given_items(self) -> set[int]:
        """The numbers of the items that the rewards may give a hero, the fallback's among them."""
        return {item for gift in self.gifts for item, _ in gift.items} | {self.fallback[0]}

    @property
    def texts(self) -> set[str]:
        """The ids of the texts that name and describe the rewards, of the game or the server."""
        texts: set[str] = set()
        for event in self.events.values():
            named = [t for part in event.parts.values() for t in (part.name, part.description) if t]
            if event.daily:
                # The day from 1 stands in a daily event's texts, and a year has at most 366.
                named = [text.replace("{day}", str(day)) for text in named for day in range(1, 367)]
            texts.update(named)
        return texts

    @model_validator(mode="after")
    def _check_parts(self) -> Self:
        for key, event in self.events.items():
            factions = [part.faction for part in event.parts.values()]
            if len(factions) != len(set(factions)):
                raise ValueError(f"the event {key} has two parts for one faction")
        return self
