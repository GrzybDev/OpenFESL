import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from openfesl.core.game_pages import Colour, Glyph

# The numbers of the items that a stat names as text, joined by semicolons; none at all when empty.
_ITEM_LIST = re.compile(r"(?:[0-9]+(?:;[0-9]+)*)?")


class Faction(BaseModel):
    """A side that a game's heroes fight for, and how the site draws it.

    Attributes:
        team: The number the game stores for it in the hero's faction stat.
        hair_styles: The item numbers of the hair styles a hero of the faction may wear, in the
            order the creator lists them; a hero may also wear none, stored as 0.
        facial_hair: The item numbers of its facial hair, likewise.
        base_items: The item numbers of what every hero of the faction holds without being
            granted it, of every class, which the server counts as held while it has no
            catalogue to tell the classes apart.
        colour: The faction's colour, behind its glyph.
        ink: The colour of the glyph and text on it.
        glyph: The faction's glyph.
    """

    model_config = ConfigDict(frozen=True)

    team: int
    hair_styles: list[PositiveInt]
    facial_hair: list[PositiveInt]
    base_items: list[PositiveInt] = []
    colour: Colour
    ink: Colour
    glyph: Glyph


class HeroClass(BaseModel):
    """A class that a game's heroes play.

    Attributes:
        kit: The number the game stores for it in the hero's class stat.
        glyph: The class's glyph.
    """

    model_config = ConfigDict(frozen=True)

    kit: int
    glyph: Glyph


class Loadout(BaseModel):
    """What a new hero of a faction and class starts with, as item numbers.

    Attributes:
        appearance: The clothes it wears.
        equipment: The items on its equipment bar.
        emotes: Its emotes.
        missions: The missions it may wish for from the start.
    """

    model_config = ConfigDict(frozen=True)

    appearance: list[int]
    equipment: list[int]
    emotes: list[int]
    missions: list[int] = []


class HeroStats(BaseModel):
    """The stats in which the game keeps a hero's choices and progress, by their keys.

    Attributes:
        faction: The faction's team number.
        hero_class: The class's kit number.
        skin_tone: The skin tone, from 1.
        hair_colour: The hair colour, from 1.
        hair_style: The hair style's item number, 0 for none.
        facial_hair: The facial hair's item number, 0 for none.
        appearance: The clothes worn, as item numbers joined by semicolons.
        equipment: The equipment bar, likewise.
        emotes: The emotes, likewise.
        level: The level.
        xp: The XP gathered within the level.
        completed: How often the hero completed a mission, with {id} for the mission's number.
        daily_completed: The day of the year, from 0, whose daily mission the hero's account
            completed last; a stat of the account.
    """

    model_config = ConfigDict(frozen=True, validate_by_name=True)

    faction: str
    hero_class: str = Field(alias="class")
    skin_tone: str
    hair_colour: str
    hair_style: str
    facial_hair: str
    appearance: str
    equipment: str
    emotes: str
    level: str
    xp: str
    completed: str
    daily_completed: str


class WrittenStat(BaseModel):
    """Stats that a player's own game writes, of its hero or of its account, and their values.

    Attributes:
        keys: The pattern that the stats' keys match whole.
        items: The kinds of the items that a stat names by their numbers, 0 naming none; empty
            for a stat that holds a plain number.
        listed: Whether a stat names its items as a text of numbers joined by semicolons rather
            than one item as its number.
        highest: The highest number that a stat without items holds, from 0.
    """

    model_config = ConfigDict(frozen=True)

    keys: re.Pattern[str]
    items: frozenset[str] = frozenset()
    listed: bool = False
    highest: int = 0

    def numbers(self, value: float, text: str) -> list[int] | None:
        """Return the numbers that a write of the stat holds, or None for one it cannot hold.

        Args:
            value: The number written.
            text: The text written.
        """
        if self.listed:
            if value or not _ITEM_LIST.fullmatch(text):
                return None
            return [int(number) for number in text.split(";") if number]
        if text or not value.is_integer() or value < 0 or not (self.items or value <= self.highest):
            return None
        return [int(value)]


class HeroRules(BaseModel):
    """How a game's heroes are made and kept, for a game whose heroes are made on its site.

    Attributes:
        slots: The heroes every account may have without buying further slots.
        name: The pattern that a hero's whole name matches, which the forms check too.
        name_length: The shortest and longest names, which the forms say.
        factions: The factions, in the order the creator shows them.
        classes: The classes, in the order the creator shows them.
        skin_tones: The colour of each skin tone the site draws, from tone 1.
        skin_order: The order in which the creator lists the skin tones.
        hair_colours: The colour of each hair colour the site draws, from colour 1.
        hair_order: The order in which the creator lists the hair colours.
        stats: The keys of the stats that keep a hero's choices and progress.
        starting_stats: Further stats every new hero starts with, by key.
        loadouts: What a new hero starts with, by faction and class.
        starting_items: The items a new hero is granted with their uses, by their Magma tag:
            those the game does not grant itself.
        written: The stats that a player's own game writes, of its hero and of its account.
        granted: The kinds of items that the game grants every hero itself: each item of the
            kind that fits the hero's faction and class, once the hero reaches its level.
        highest_level: The highest level a hero reaches; its level stays between 1 and this.
        points: The currency of the points each hero earns with its levels and spends alone.
        points_every: How many levels earn a hero one of its points: one on each level that is a
            multiple of this; none where the game's servers credit them instead.
        currency: The currency, which an account's heroes share, that slots and new names are
            paid in.
        rename_price: What a new name costs.
        slot_price: What a further hero slot costs.
        running_low: The uses left at which an item limited by uses runs low.
        running_low_days: The days left at which a rental runs low; one with hours left does.
    """

    model_config = ConfigDict(frozen=True)

    slots: PositiveInt
    name: str
    name_length: tuple[PositiveInt, PositiveInt]
    factions: dict[str, Faction]
    classes: dict[str, HeroClass]
    skin_tones: list[Colour]
    skin_order: list[PositiveInt]
    hair_colours: list[Colour]
    hair_order: list[PositiveInt]
    stats: HeroStats
    starting_stats: dict[str, float] = {}
    loadouts: dict[str, dict[str, Loadout]]
    starting_items: dict[str, PositiveInt] = {}
    written: list[WrittenStat] = []
    granted: frozenset[str] = frozenset()
    highest_level: PositiveInt
    points: str
    points_every: PositiveInt | None = None
    currency: str
    rename_price: PositiveInt
    slot_price: PositiveInt
    running_low: PositiveInt
    running_low_days: PositiveInt

    def faction_of(self, team: int | None) -> str | None:
        """Return the faction whose team number a hero's faction stat holds, if it is one.

        Args:
            team: The stat's number; none when the hero has no such stat.
        """
        return next((name for name, faction in self.factions.items() if faction.team == team), None)

    def class_of(self, kit: int | None) -> str | None:
        """Return the class whose kit number a hero's class stat holds, if it is one.

        Args:
            kit: The stat's number; none when the hero has no such stat.
        """
        return next((name for name, found in self.classes.items() if found.kit == kit), None)

    def written_stat(self, key: str) -> WrittenStat | None:
        """Return how a player's own game writes a stat, if it writes it at all.

        Args:
            key: The stat's key.
        """
        return next((stat for stat in self.written if stat.keys.fullmatch(key)), None)

    @model_validator(mode="after")
    def _check_lists(self) -> Self:
        if sorted(self.skin_order) != list(range(1, len(self.skin_tones) + 1)):
            raise ValueError("skin_order must list every skin tone once")
        if sorted(self.hair_order) != list(range(1, len(self.hair_colours) + 1)):
            raise ValueError("hair_order must list every hair colour once")
        if set(self.loadouts) != set(self.factions):
            raise ValueError("every faction needs its loadouts, and only factions have them")
        for name in self.factions:
            if set(self.loadouts[name]) != set(self.classes):
                raise ValueError(f"{name} needs a loadout for each class")
        return self
