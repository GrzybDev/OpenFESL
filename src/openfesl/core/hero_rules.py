from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from openfesl.core.game_pages import Colour, Glyph


class Faction(BaseModel):
    """A side that a game's heroes fight for, and how the site draws it.

    Attributes:
        team: The number the game stores for it in the hero's faction stat.
        hair_styles: The item numbers of the hair styles a hero of the faction may wear, in the
            order the creator lists them; a hero may also wear none, stored as 0.
        facial_hair: The item numbers of its facial hair, likewise.
        colour: The faction's colour, behind its glyph.
        ink: The colour of the glyph and text on it.
        glyph: The faction's glyph.
    """

    model_config = ConfigDict(frozen=True)

    team: int
    hair_styles: list[PositiveInt]
    facial_hair: list[PositiveInt]
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
    """

    model_config = ConfigDict(frozen=True)

    appearance: list[int]
    equipment: list[int]
    emotes: list[int]


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
        points: The currency of the points each hero earns with its levels and spends alone.
        currency: The currency, which an account's heroes share, that new names are paid in.
        rename_price: What a new name costs.
        running_low: The uses left at which an item limited by uses counts as running low.
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
    points: str
    currency: str
    rename_price: PositiveInt
    running_low: PositiveInt

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
