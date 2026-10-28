import asyncio
import logging
import secrets
from collections import ChainMap
from collections.abc import Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.admins import Admin
from openfesl.core.catalogue import (
    CatalogueDocument,
    CatalogueFile,
    CatalogueItem,
    CatalogueRules,
    text_id,
)
from openfesl.core.games import Game, Games
from openfesl.core.stats import partition_of
from openfesl.core.texts import BASE_LANGUAGE

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredCatalogue:
    """A game's imported catalogue as the server keeps it, texts aside.

    Attributes:
        document: The catalogue.
        revision: The import it comes from, which names the folder of its icons.
        icons: How many of its icons the server keeps.
    """

    document: CatalogueDocument
    revision: str
    icons: int


class CatalogueStore(Protocol):
    """Where the games' imported catalogues are kept, one per game."""

    async def current(self, partition: str) -> StoredCatalogue | None:
        """Return a game's catalogue without its texts, if it was imported.

        Args:
            partition: The game's partition.

        Raises:
            ValueError: When the kept catalogue is of a layout this server does not read.
        """

    async def texts(self, partition: str, language: str) -> dict[str, str]:
        """Return the texts of a game's catalogue in a language by id; empty without any.

        Args:
            partition: The game's partition.
            language: The language code.
        """

    async def replace(
        self,
        partition: str,
        game: str,
        catalogue: StoredCatalogue,
        texts: Mapping[str, Mapping[str, str]],
        admin: Admin | None,
    ) -> None:
        """Replace a game's catalogue and all its texts at once, and record the import.

        Args:
            partition: The game's partition.
            game: The game's data-file name, which the admin's log names.
            catalogue: The new catalogue.
            texts: Its texts by language code, each by id.
            admin: The admin who imported it; None for the command line.
        """

    def importing(self, partition: str) -> AbstractAsyncContextManager[None]:
        """Hold off other imports of a game's catalogue, on any server, until the block ends.

        Args:
            partition: The game's partition.
        """


class IconStore(Protocol):
    """Where the icons of the games' imported catalogues are kept, a folder per import."""

    async def write(self, game: str, revision: str, icons: Mapping[str, bytes]) -> None:
        """Keep the icons of an import.

        Args:
            game: The game's data-file name.
            revision: The import.
            icons: The icons as WebP pictures, by name.
        """

    async def drop(self, game: str, revision: str) -> None:
        """Remove the icons of an import.

        Args:
            game: The game's data-file name.
            revision: The import.
        """

    async def keep_only(self, game: str, revision: str) -> None:
        """Remove the icons of a game's other imports.

        Args:
            game: The game's data-file name.
            revision: The import whose icons stay.
        """


@dataclass(frozen=True, slots=True)
class Item:
    """An item of a game's catalogue, with what its definition means.

    Attributes:
        id: Its number.
        kind: Its kind, such as weapon.
        slot: The slot it is worn in, for an item that is worn.
        factions: The factions whose heroes may use it.
        classes: The classes that may use it.
        level: The lowest level that may use it, when its definition names one.
        levels: The numbers of the levels it is one of, in order; empty for an item alone.
        icon: The name of its icon, if the game has one.
        fields: The values of its definition that the game's rules read, as the definitions
            name them.
        names: The ids of its name in the language table, tried in order.
        base: Whether every hero it fits holds it without being granted it.
        prerequisite: The mission a hero completes before it may take this one, if any.
        repeatable: Whether a hero may complete it again, for a mission.
        withheld: Whether the store never sells nor drops it, rewards never give it and only
            the admin panel shows its icon.
    """

    id: int
    kind: str
    slot: str | None
    factions: frozenset[str]
    classes: frozenset[str]
    level: int | None
    levels: tuple[int, ...]
    icon: str | None
    fields: Mapping[str, str]
    names: tuple[str, ...]
    base: bool
    prerequisite: int | None
    repeatable: bool
    withheld: bool


@dataclass(frozen=True, slots=True)
class Contents:
    """What a game's imported catalogue holds besides its texts.

    Attributes:
        revision: The import it comes from; none before the first.
        icons: How many of its icons the server keeps.
        items: The items by number.
        daily_missions: The number of each day's daily mission, by the day of the year.
        experience: The XP from each level to the next, by level.
    """

    revision: str | None = None
    icons: int = 0
    items: Mapping[int, Item] = field(default_factory=dict)
    daily_missions: tuple[int, ...] = ()
    experience: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Catalogue:
    """A game's catalogue as the server imported it, with its texts in one language.

    Before the first import it holds nothing, and pages show items by their numbers.

    Attributes:
        contents: What it holds besides its texts.
        texts: Its texts by the form of their ids that text_id gives, in its language and
            otherwise in English.
    """

    contents: Contents = field(default_factory=Contents)
    texts: Mapping[str, str] = field(default_factory=dict)

    @property
    def imported(self) -> bool:
        """Whether the game's catalogue was imported."""
        return self.contents.revision is not None

    def item(self, item_id: int) -> Item | None:
        """Return an item by its number, if the catalogue holds it.

        Args:
            item_id: The item's number.
        """
        return self.contents.items.get(item_id)

    def items(self, kind: str) -> list[Item]:
        """Return the items of a kind, by number.

        Args:
            kind: The kind, such as weapon.
        """
        return sorted(
            (item for item in self.contents.items.values() if item.kind == kind),
            key=lambda item: item.id,
        )

    def name(self, item_id: int) -> str | None:
        """Return an item's name, if the catalogue has one for it.

        Args:
            item_id: The item's number.
        """
        item = self.item(item_id)
        names = item.names if item else ()
        return next((self.texts[name] for name in names if name in self.texts), None)

    def text(self, name: str) -> str | None:
        """Return a text of the game's language table, if the catalogue holds it.

        Args:
            name: The text's id, in any case.
        """
        return self.texts.get(text_id(name))

    def daily_mission(self, day: date) -> int | None:
        """Return the number of the daily mission of a day, as the game picks it.

        Args:
            day: The day in UTC.
        """
        missions = self.contents.daily_missions
        index = day.timetuple().tm_yday - 1
        return missions[index] if index < len(missions) else None

    def xp_to_next(self, level: int) -> int | None:
        """Return the XP a hero needs from a level to the next; none from the highest.

        Args:
            level: The hero's level.
        """
        experience = self.contents.experience
        return experience[level] if 0 < level < len(experience) else None


@dataclass(frozen=True, slots=True)
class ImportedCatalogue:
    """What an import brought.

    Attributes:
        game: The game whose catalogue it replaced.
        items: How many items the catalogue has.
        languages: In how many languages it has texts.
        icons: How many of its icons the server keeps; none where the deployment keeps no files.
    """

    game: Game
    items: int
    languages: int
    icons: int


class Catalogues:
    """The games' catalogues as the server imported them from players' copies, held in memory.

    The texts of each language are read once a page or the game asks for them.
    """

    def __init__(self, store: CatalogueStore, icons: IconStore | None, games: Games) -> None:
        """Keep the catalogues in stores.

        Args:
            store: Where the catalogues and their texts are kept.
            icons: Where their icons are kept; none where the deployment keeps no files.
            games: The games the server serves.
        """
        self._store = store
        self._icons = icons
        self._games = games
        self._contents: dict[str, Contents] = {}
        self._texts: dict[tuple[str, str], tuple[str, dict[str, str]]] = {}

    async def load(self) -> None:
        """Read every game's catalogue, as when the server starts."""
        for game in self._games:
            await self._load(game)

    def follow(self, feed: AdminFeed) -> None:
        """Read a game's catalogue again once the admin imported a new one elsewhere.

        Args:
            feed: The admin's actions as the log records them.
        """
        feed.on(AdminAction.CATALOGUE_IMPORT, self._imported)

    def contents(self, game: Game) -> Contents:
        """Return what a game's imported catalogue holds besides its texts; nothing before then.

        Args:
            game: The game.
        """
        return self._contents.get(game.id, Contents())

    async def of(self, game: Game, language: str) -> Catalogue:
        """Return a game's catalogue with its texts in a language, in English where it has none.

        Args:
            game: The game.
            language: The language code, such as pl.
        """
        contents = self.contents(game)
        if contents.revision is None:
            return Catalogue(contents)
        partition, revision = partition_of(game), contents.revision
        found = [
            await self._language(game, partition, revision, code)
            for code in dict.fromkeys((language, BASE_LANGUAGE))
        ]
        return Catalogue(contents, ChainMap(*found))

    async def import_file(self, data: bytes, admin: Admin | None) -> ImportedCatalogue:
        """Replace a game's catalogue with the one a file holds.

        The catalogue and all its texts change at once, and the icons of earlier imports are
        removed once they did.

        Args:
            data: The file that `openfesl catalogue extract` wrote.
            admin: The admin who imports it; None for the command line.

        Raises:
            ValueError: When the file holds no catalogue that this server reads, or one of a
                game that it serves without a catalogue.
        """
        catalogue = await asyncio.to_thread(CatalogueFile.read, data)
        game_id = catalogue.document.game
        game = self._games.by_id(game_id)
        if game is None or game.catalogue is None:
            raise ValueError(f"the server serves no game {game_id} with a catalogue")
        revision = secrets.token_hex(8)
        icons = self._icons if catalogue.icons else None
        stored = StoredCatalogue(catalogue.document, revision, len(catalogue.icons) if icons else 0)
        partition = partition_of(game)
        # Imports of a game take turns, as each removes the icons of every other import.
        async with self._store.importing(partition):
            if icons is not None:
                await icons.write(game.id, revision, catalogue.icons)
            try:
                await self._store.replace(partition, game.id, stored, catalogue.texts, admin)
            except BaseException:
                if icons is not None:
                    await icons.drop(game.id, revision)
                raise
            if self._icons is not None:
                await self._icons.keep_only(game.id, revision)
        self._contents[game.id] = _contents(game.catalogue, stored)
        return ImportedCatalogue(
            game, len(catalogue.document.items), len(catalogue.texts), stored.icons
        )

    async def _imported(self, action: LoggedAction) -> None:
        game = self._games.by_id(action.target)
        if game is not None:
            await self._load(game)

    async def _load(self, game: Game) -> None:
        if game.catalogue is None:
            return
        try:
            stored = await self._store.current(partition_of(game))
        except ValueError:
            logger.exception("The catalogue of %s cannot be read; it needs a new import", game.name)
            stored = None
        self._contents[game.id] = _contents(game.catalogue, stored) if stored else Contents()

    async def _language(
        self, game: Game, partition: str, revision: str, language: str
    ) -> dict[str, str]:
        cached = self._texts.get((game.id, language))
        if cached is not None and cached[0] == revision:
            return cached[1]
        stored = await self._store.texts(partition, language)
        texts = {text_id(name): text for name, text in stored.items()}
        self._texts[(game.id, language)] = (revision, texts)
        return texts


def catalogue_items(rules: CatalogueRules, items: Iterable[CatalogueItem]) -> dict[int, Item]:
    """Return the items of a game's catalogue with what their definitions mean, by number.

    Args:
        rules: The rules of the game's catalogue, which say what the definitions mean.
        items: The items as the catalogue holds them.
    """
    names = {item_file.kind: item_file.name for item_file in rules.items}
    slots = {number: name for name, number in rules.slots.items()}
    found = {}
    for item in items:
        values = item.fields
        slot = _number(values.get(rules.fields.slot))
        template = names.get(item.kind, "")
        # A level after the first is often named by the first one alone.
        numbers = dict.fromkeys((item.id, *item.levels[:1]))
        prerequisite = rules.fields.prerequisite
        found[item.id] = Item(
            id=item.id,
            kind=item.kind,
            slot=slots.get(slot) if slot is not None else None,
            factions=_factions(values, rules),
            classes=_named(values.get(rules.fields.classes), rules.classes),
            level=_number(values.get(rules.fields.level)),
            levels=item.levels,
            icon=item.icon,
            fields=values,
            names=tuple(text_id(template.replace("{id}", str(n))) for n in numbers if template),
            base=_flag(values, rules.fields.base, missing=False),
            prerequisite=(_number(values.get(prerequisite)) or None) if prerequisite else None,
            repeatable=_flag(values, rules.fields.repeatable, missing=True),
            withheld=item.id in rules.withheld,
        )
    return found


def _contents(rules: CatalogueRules, stored: StoredCatalogue) -> Contents:
    document = stored.document
    found = catalogue_items(rules, document.items)
    return Contents(
        stored.revision, stored.icons, found, document.daily_missions, document.experience
    )


def _number(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _flag(values: Mapping[str, str], field: str | None, missing: bool) -> bool:
    # Definitions write their flags as true or false, in any case.
    text = values.get(field) if field else None
    return missing if text is None else text.strip().lower() == "true"


def _factions(values: Mapping[str, str], rules: CatalogueRules) -> frozenset[str]:
    team = _number(values.get(rules.fields.team))
    if team:
        return frozenset(name for name, number in rules.teams.items() if number == team)
    return _named(values.get(rules.fields.factions), rules.factions)


def _named(mask: str | None, bits: Mapping[str, int]) -> frozenset[str]:
    number = _number(mask)
    if number is None:
        return frozenset(bits)
    return frozenset(name for name, bit in bits.items() if number & bit)
