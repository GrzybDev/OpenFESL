import json
from dataclasses import dataclass, field
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from openfesl.core.hero_models import HeroModels, ModelRules
from openfesl.core.texts import BASE_LANGUAGE
from openfesl.formats.catalogue_archive import CatalogueArchive, read_archive, write_archive

# The version of the catalogue's document; a server imports only its own.
FORMAT: Final = 1
_TEXTS = TypeAdapter(dict[str, str])


def text_id(name: str) -> str:
    """Return the form in which the server looks up an id of a game's language table.

    Games look their texts up regardless of case, and some ids hold stray spaces.

    Args:
        name: The text's id as the table or a definition writes it.
    """
    return "".join(name.split()).upper()


class ItemFile(BaseModel):
    """A file of a game's item definitions and the kind of item it defines.

    Attributes:
        file: The file's name in the archive of definitions.
        kind: The kind of its items, such as weapon, which the server and its pages know them
            by.
        element: The name of the elements that define the items.
        levels: The name of the children that are a definition's levels, each an item of its
            own, if the definitions have levels.
        name: The id of each item's name in the language table, with {id} for the item's
            number; a level after the first that has no name of its own takes the first one's.
        icons: Where each item's icon may lie in the archive of icons, tried in order, with
            {id} for the item's number and {first} for its first level's.
    """

    model_config = ConfigDict(frozen=True)

    file: str
    kind: str
    element: str = "item"
    levels: str | None = None
    name: str
    icons: list[str] = []


class ItemFields(BaseModel):
    """The fields of an item's definition that say who may use the item.

    Attributes:
        factions: The bit mask of the factions whose heroes may use it; it fits every faction
            when its definition lacks the field.
        classes: The bit mask of the classes that may use it, likewise.
        slot: The number of the slot it is worn in.
        level: The lowest level that may use it.
        team: The id of the one team whose heroes may use it, which missions give instead of
            a faction mask; 0 fits either team.
        base: The flag of an item that every hero it fits holds without being granted it, if
            the definitions mark such items.
        prerequisite: The number of the mission that a hero completes before it may take this
            one, 0 for none, if the definitions name one.
        repeatable: The flag that is false for a mission that a hero completes only once, if
            the definitions say so.
    """

    model_config = ConfigDict(frozen=True)

    factions: str
    classes: str
    slot: str
    level: str
    team: str
    base: str | None = None
    prerequisite: str | None = None
    repeatable: str | None = None


class DailyMissions(BaseModel):
    """The table of each day's daily mission in a game's executable.

    Attributes:
        after: The string that the code reading the table pushes right before it reads.
        days: How many days the table holds, from 1 January.
    """

    model_config = ConfigDict(frozen=True)

    after: str
    days: int


class Experience(BaseModel):
    """The XP from each level to the next, which a game's executable sets one level per call.

    Attributes:
        levels: How many levels the table holds, from level 0.
    """

    model_config = ConfigDict(frozen=True)

    levels: int


class CatalogueRules(BaseModel):
    """Where a copy of a game keeps its catalogue of items, and what the definitions mean.

    Paths are relative to the folder of the game's executable.

    Attributes:
        definitions: The archive of the item definitions and the tables beside them.
        texts: The language table, which names the items in each language.
        icons: The archive that holds the items' icons.
        languages: The language code of each of the language table's columns, by the column's
            name; English is needed, as every other language falls back to it.
        items: The files of item definitions in the archive of definitions.
        tables: Further files of the archive of definitions, kept whole.
        withheld: The items that the store never sells nor drops and rewards never give, whose
            icons only the admin panel shows.
        fields: The fields of the definitions that say who may use an item.
        factions: The factions by the bits that stand for them in a faction mask.
        teams: The factions by the ids of their teams.
        classes: The classes by the bits that stand for them in a class mask.
        slots: The slots by their numbers.
        daily_missions: The table of each day's daily mission in the executable.
        experience: The table of the XP from each level to the next in the executable.
        models: Where the copy keeps the models that the game draws its heroes with, if the
            catalogue carries them.
    """

    model_config = ConfigDict(frozen=True)

    definitions: str
    texts: str
    icons: str
    languages: dict[str, str]
    items: list[ItemFile]
    tables: list[str] = []
    withheld: frozenset[int] = frozenset()
    fields: ItemFields
    factions: dict[str, int]
    teams: dict[str, int]
    classes: dict[str, int]
    slots: dict[str, int]
    daily_missions: DailyMissions
    experience: Experience
    models: ModelRules | None = None

    @model_validator(mode="after")
    def _check_english(self) -> Self:
        if BASE_LANGUAGE not in self.languages.values():
            raise ValueError(f"the language table needs a column for {BASE_LANGUAGE}")
        return self

    @model_validator(mode="after")
    def _check_models(self) -> Self:
        if self.models and not set(self.models.figures) <= set(self.factions):
            raise ValueError("the heroes' models name a faction that the catalogue lacks")
        if self.models and self.models.items not in {found.file for found in self.items}:
            raise ValueError("the heroes' models name a file that defines no items")
        return self


class CatalogueItem(BaseModel):
    """An item as a game's catalogue holds it, missions included.

    Attributes:
        id: Its number, unique in the game.
        kind: Its kind, from the file that defines it.
        fields: The values of its definition that the game's rules read, as the game's
            definitions file names them.
        levels: The numbers of the levels of the definition it is one level of, in order;
            empty for an item defined alone.
        icon: The name of its icon among the catalogue's icons, if the game has one.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    kind: str
    fields: dict[str, str] = {}
    levels: tuple[int, ...] = ()
    icon: str | None = None


class Element(BaseModel):
    """An element of an XML file that a catalogue keeps whole."""

    model_config = ConfigDict(frozen=True)

    tag: str
    attributes: dict[str, str] = {}
    text: str = ""
    children: tuple[Element, ...] = ()


class CatalogueDocument(BaseModel):
    """A game's catalogue as its archive carries it and the server stores it, texts aside.

    Attributes:
        format: The version of this document's layout.
        game: The data-file name of the game.
        items: Every item the game defines.
        tables: Further files of the game's definitions, by file name.
        daily_missions: The number of each day's daily mission, by the day of the year from
            1 January as 0.
        experience: The XP that a hero needs from each level to the next, by level from 0.
        models: What the game draws its heroes with, if the catalogue carries it.
    """

    model_config = ConfigDict(frozen=True)

    format: Literal[1]
    game: str
    items: tuple[CatalogueItem, ...]
    tables: dict[str, Element] = {}
    daily_missions: tuple[int, ...] = ()
    experience: tuple[int, ...] = ()
    models: HeroModels | None = None


@dataclass(frozen=True, slots=True)
class CatalogueFile:
    """A game's catalogue whole, as a file carries it from a copy of the game to a server.

    Attributes:
        document: The catalogue but its texts, icons, models and textures.
        texts: Its texts by language code, each by id as the language table writes it.
        icons: Its icons as WebP pictures, by name.
        models: The models of its heroes as binary glTF files, by name.
        textures: The textures of its heroes' models as WebP pictures, by name.
    """

    document: CatalogueDocument
    texts: dict[str, dict[str, str]]
    icons: dict[str, bytes]
    models: dict[str, bytes] = field(default_factory=dict)
    textures: dict[str, bytes] = field(default_factory=dict)

    @classmethod
    def read(cls, data: bytes) -> Self:
        """Read a catalogue's file.

        Args:
            data: The file.

        Raises:
            ValueError: When the file holds no catalogue that this server reads.
        """
        archive = read_archive(data)
        return cls(
            CatalogueDocument.model_validate_json(archive.document),
            {language: _TEXTS.validate_json(texts) for language, texts in archive.texts.items()},
            archive.icons,
            archive.models,
            archive.textures,
        )

    def write(self) -> bytes:
        """Return the catalogue's file, the same bytes for the same catalogue."""
        texts = {
            language: json.dumps(found, ensure_ascii=False, sort_keys=True).encode()
            for language, found in self.texts.items()
        }
        document = self.document.model_dump_json(exclude_defaults=True).encode()
        return write_archive(
            CatalogueArchive(document, texts, self.icons, self.models, self.textures)
        )
