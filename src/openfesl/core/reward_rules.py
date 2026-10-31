from datetime import date, datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveInt, model_validator

from openfesl.core.calendar import Window, Zone, today
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


class Goal(BaseModel):
    """What one hero must reach within its event for a part of the event to be claimed.

    Attributes:
        stat: The hero's stat that counts, from the event's start.
        count: What the hero's count must reach.
        hero_class: The class of the heroes that count and that the gift goes to; any when none.
        progress: The id of the server's text that says how far a hero got, with %(count)s and
            %(goal)s, such as 112 of 700 kills.
    """

    model_config = ConfigDict(frozen=True)

    stat: str
    count: PositiveInt
    hero_class: str | None = None
    progress: str = "web.rewards.progress"


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
        goal: What one hero of the faction must reach first; nothing when none.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    faction: str | None = None
    name: str
    description: str | None = None
    gift: Gift
    days: dict[PositiveInt, Gift] = {}
    goal: Goal | None = None

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
        title: The id of the server's text that names it, such as Halloween trick-or-treat.
        summary: The id of the server's text that says in a sentence what it gives.
        window: The days of each year it runs on.
        daily: Whether each of its days gives a gift of its own, claimed on that day, rather
            than one gift for all of them.
        parts: Its parts, by name.
        stated: What the texts of its parts state, so that changing it needs own text: days (a
            text for each of the data's days), end (its last day) or gift (what the parts give).
        own_text: The admin's own text of what it gives, by language, in place of the texts
            of its parts, which its title then names; English stands in for a language without
            one. None keeps the parts' texts.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    title: str
    summary: str
    window: Window
    daily: bool = False
    parts: dict[str, Part]
    stated: frozenset[Literal["days", "end", "gift"]] = frozenset()
    own_text: dict[str, str] = {}

    def text(self, language: str) -> str:
        """Return the admin's own text of the event in a language, else in English.

        Args:
            language: The language's code.
        """
        return self.own_text.get(language) or self.own_text.get("en", "")

    def outdated(self, edited: Event, year: int) -> list[str]:
        """Return what the texts of the event's parts state that an edit of it changes.

        Args:
            edited: The event as edited.
            year: The year whose runs are compared.
        """
        first, last = self.window.days(year)
        new_first, new_last = edited.window.days(year)
        found = []
        if "days" in self.stated and new_last - new_first > last - first:
            found.append("days")
        if "end" in self.stated and new_last != last:
            found.append("end")
        gifts = [(part.gift, part.days) for part in self.parts.values()]
        if "gift" in self.stated and gifts != [
            (part.gift, part.days) for part in edited.parts.values()
        ]:
            found.append("gift")
        return found


class StarterPacks(BaseModel):
    """What a new hero of each faction and class may claim once, for a while after its creation.

    Attributes:
        enabled: Whether they are given.
        days: How many days after the day of its creation a hero may claim its pack, until the
            last one's end.
        name: The id of their name in the game's texts or the server's own.
        description: The id of the text about them, likewise.
        packs: What each faction's heroes get, by class.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    days: PositiveInt
    name: str
    description: str | None = None
    packs: dict[str, dict[str, Gift]]

    def last_day(self, created: datetime, zone: str) -> date:
        """Return the last day on which a hero may claim its pack.

        Args:
            created: When the hero was created.
            zone: The time zone whose midnights the days start at.
        """
        return today(created, zone) + timedelta(days=self.days)


class Milestone(BaseModel):
    """What an account gets once its heroes reach a goal, or once for each hero that does.

    Attributes:
        enabled: Whether it is given.
        name: The id of its name in the game's texts or the server's own.
        description: The id of the text about it, likewise.
        stats: The heroes' stats whose values it adds up; it counts the heroes when none.
        each: Whether each hero counts on its own, so that it is given for each hero that
            reaches the goal, rather than once for the account's heroes together.
        goal: What the count reaches it at.
        gift: What it gives, which goes to the account.
        progress: The id of the server's text that says how far the count got, with %(count)s
            and %(goal)s, such as Level 3 of 10.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    name: str
    description: str | None = None
    stats: tuple[str, ...] = ()
    each: bool = False
    goal: PositiveInt
    gift: Gift
    progress: str = "web.rewards.progress"

    @model_validator(mode="after")
    def _check_gift(self) -> Self:
        if self.gift.for_hero:
            raise ValueError(f"the milestone {self.name} gives the account alone")
        return self


class FirstChest(BaseModel):
    """What an account gets once, after a dedicated server stacked a first chest on its heroes.

    Attributes:
        enabled: Whether it is given.
        name: The id of its name in the game's texts or the server's own.
        description: The id of the text about it, likewise.
        gift: What it gives, which goes to the account.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    name: str
    description: str | None = None
    gift: Gift


class RewardRules(BaseModel):
    """How a game's site gives rewards that accounts claim, as its data sets them.

    Attributes:
        paused: Whether claims are paused, while everything else goes on.
        zone: The time zone at whose midnight the days start.
        currency: The currency of the Funds that rewards give.
        fallback: What a hero gets for an item of a gift that it owns for good already.
        events: The events of the yearly calendar, by key.
        starter: The packs that new heroes may claim; none for a game without them.
        milestones: The goals that bring the account a gift, by key.
        first_chest: What the account gets after its first treasure chest; nothing when none.
    """

    model_config = ConfigDict(frozen=True)

    paused: bool = False
    zone: Zone = "UTC"
    currency: str
    fallback: DropEntry
    events: dict[str, Event] = {}
    starter: StarterPacks | None = None
    milestones: dict[str, Milestone] = {}
    first_chest: FirstChest | None = None

    @property
    def gifts(self) -> list[Gift]:
        """Every gift of the rewards: of each event and day, pack, milestone and the first chest."""
        parts = [part for event in self.events.values() for part in event.parts.values()]
        gifts = [gift for part in parts for gift in (part.gift, *part.days.values())]
        packs = self.starter.packs if self.starter else {}
        gifts += [gift for classes in packs.values() for gift in classes.values()]
        gifts += [milestone.gift for milestone in self.milestones.values()]
        gifts += [self.first_chest.gift] if self.first_chest else []
        return gifts

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
        others = [self.starter, *self.milestones.values(), self.first_chest]
        texts.update(t for found in others if found for t in (found.name, found.description) if t)
        return texts

    @model_validator(mode="after")
    def _check_parts(self) -> Self:
        for key, event in self.events.items():
            factions = [part.faction for part in event.parts.values()]
            if len(factions) != len(set(factions)):
                raise ValueError(f"the event {key} has two parts for one faction")
            if event.daily and any(part.goal for part in event.parts.values()):
                raise ValueError(f"the event {key} has goals on its days")
            if event.window.overlaps:
                raise ValueError(f"the runs of the event {key} in two years overlap")
            if event.own_text and not event.own_text.get("en"):
                raise ValueError(f"the own text of the event {key} lacks English")
        return self
