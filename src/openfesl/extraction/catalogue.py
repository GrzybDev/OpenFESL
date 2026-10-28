import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from openfesl.core.catalogue import (
    FORMAT,
    CatalogueDocument,
    CatalogueFile,
    CatalogueItem,
    CatalogueRules,
    Element,
    text_id,
)
from openfesl.core.catalogues import catalogue_items
from openfesl.core.games import Game, Games
from openfesl.core.hero_rules import HeroRules
from openfesl.core.texts import BASE_LANGUAGE
from openfesl.extraction.copies import ExtractionError, archive, member, members, path
from openfesl.extraction.hero_models import extract_models
from openfesl.formats.definitions import read_definitions, read_tree
from openfesl.formats.executable import Executable
from openfesl.formats.icons import webp
from openfesl.formats.language_table import read_language_table

_ICON_NAME = re.compile(r"[a-z0-9_]{1,64}")


def find_game(games: Games, folder: Path) -> Game:
    """Return the game with a catalogue whose executable a folder holds.

    Args:
        games: The games the server serves.
        folder: The folder of a copy of a game.

    Raises:
        ExtractionError: When the folder holds the executable of no such game.
    """
    for game in games:
        if game.catalogue is not None and (folder / game.executable).is_file():
            return game
    raise ExtractionError(f"{folder} holds no copy of a game whose catalogue can be extracted")


def extract(game: Game, folder: Path) -> CatalogueFile:
    """Read a game's catalogue from a copy of the game, which stays as it is.

    Args:
        game: The game.
        folder: The folder of the copy's executable.

    Returns:
        What the server uses of the catalogue, converted for browsers: the fields of the items
        that the game's rules read, the texts that the rules name in each language of the
        language table, the icons of the items that the site may show, and the models that its
        creator dresses heroes in, where the rules name them.

    Raises:
        ExtractionError: When the copy lacks a file of the catalogue or a file is not as the
            game's rules describe it.
    """
    rules = game.catalogue
    if rules is None:
        raise ExtractionError(f"{game.name} has no catalogue")
    items, tables = _definitions(rules, folder, _read_fields(game))
    texts = _texts(rules, folder, _named(game, items))
    items, icons = _icons(rules, folder, items, _shown(game, items, texts[BASE_LANGUAGE]))
    daily_missions, experience = _executable(rules, folder / game.executable, items)
    dressed = _dressed(game.heroes) if game.heroes else set()
    models, files, textures = (
        extract_models(rules, folder, items, dressed) if rules.models else (None, {}, {})
    )
    document = CatalogueDocument(
        format=FORMAT,
        game=game.id,
        items=tuple(items),
        tables=tables,
        daily_missions=daily_missions,
        experience=experience,
        models=models,
    )
    return CatalogueFile(document, texts, icons, files, textures)


def _read_fields(game: Game) -> set[str]:
    # The fields of the definitions that the game's rules read: who may use an item, the values
    # that the store's prices match, and the layer that older copies set for all of an item's
    # parts.
    rules = game.catalogue
    fields = {name for name in rules.fields.model_dump().values() if name} if rules else set()
    prices = game.store.prices if game.store else []
    fields |= {field for rule in prices for field in rule.fields}
    return fields | ({rules.models.layer} if rules and rules.models else set())


def _definitions(
    rules: CatalogueRules, folder: Path, read: set[str]
) -> tuple[list[CatalogueItem], dict[str, Element]]:
    with archive(folder, rules.definitions) as definitions:
        items: dict[int, CatalogueItem] = {}
        for item_file in rules.items:
            data = member(definitions, rules.definitions, item_file.file)
            try:
                found = read_definitions(data, item_file.element, item_file.levels)
            except ValueError as error:
                raise ExtractionError(f"{item_file.file}: {error}") from None
            for definition in found:
                if definition.id in items:
                    raise ExtractionError(f"two items have the number {definition.id}")
                items[definition.id] = CatalogueItem(
                    id=definition.id,
                    kind=item_file.kind,
                    fields={key: definition.fields[key] for key in read & definition.fields.keys()},
                    levels=definition.levels,
                )
        tables = {}
        for name in rules.tables:
            try:
                tree = read_tree(member(definitions, rules.definitions, name))
            except ValueError as error:
                raise ExtractionError(f"{name}: {error}") from None
            tables[name] = Element.model_validate(tree)
    return list(items.values()), tables


def _named(game: Game, found: Sequence[CatalogueItem]) -> set[str]:
    # The texts that the site and Magma show, by the ids that text_id gives: the items' names,
    # the store's descriptions of them, and the texts of the hot deals and rewards.
    rules = game.catalogue
    if rules is None:
        return set()
    items = catalogue_items(rules, found)
    texts = game.deals.texts if game.deals else set()
    texts |= game.rewards.texts if game.rewards else set()
    if store := game.store:
        texts |= {text.replace("{id}", str(item)) for text in store.descriptions for item in items}
    return {name for item in items.values() for name in item.names} | {text_id(t) for t in texts}


def _texts(rules: CatalogueRules, folder: Path, named: set[str]) -> dict[str, dict[str, str]]:
    table = path(folder, rules.texts)
    try:
        columns = read_language_table(table.read_bytes())
    except UnicodeError:
        raise ExtractionError(f"{table} is no UTF-16 language table") from None
    except OSError:
        raise ExtractionError(f"{table} cannot be read") from None
    # An older copy may have a language's column without its texts.
    texts = {
        language: {name: text for name, text in columns[column].items() if text_id(name) in named}
        for column, language in rules.languages.items()
        if columns.get(column)
    }
    if BASE_LANGUAGE not in texts:
        raise ExtractionError(f"{table} has no column of English texts")
    return texts


def _shown(game: Game, found: Sequence[CatalogueItem], english: Mapping[str, str]) -> set[int]:
    # The items whose icons the site may show: what heroes start with or the game grants them,
    # what the store sells, which is only what the game names, or its boxes drop, what rewards
    # give, and the withheld items, whose icons the admin panel shows.
    rules = game.catalogue
    if rules is None:
        return set()
    items = catalogue_items(rules, found)
    named = {text_id(name) for name in english}
    shown = set(rules.withheld)
    if heroes := game.heroes:
        shown |= {item for faction in heroes.factions.values() for item in faction.base_items}
        loadouts = [loadout for classes in heroes.loadouts.values() for loadout in classes.values()]
        for loadout in loadouts:
            shown |= {*loadout.appearance, *loadout.equipment, *loadout.emotes, *loadout.missions}
        shown |= {int(tag.partition("-")[0]) for tag in heroes.starting_items}
        shown |= {item.id for item in items.values() if item.kind in heroes.granted}
    if store := game.store:
        shown |= {
            item.id
            for item in items.values()
            if store.sellable(item)
            and any(rule.sold for rule in store.rules(item))
            and not named.isdisjoint(item.names)
        }
        dropped = {item for box in store.boxes for tier in box.tiers for item, _ in tier.drops}
        dropped |= {box.fallback[0] for box in store.boxes if box.fallback}
        shown |= {item for item in dropped if item in items and store.sellable(items[item])}
    if rewards := game.rewards:
        shown |= rewards.given_items
    return shown


def _icons(
    rules: CatalogueRules, folder: Path, items: list[CatalogueItem], shown: set[int]
) -> tuple[list[CatalogueItem], dict[str, bytes]]:
    patterns = {item_file.kind: item_file.icons for item_file in rules.items}
    icons: dict[str, bytes] = {}
    with archive(folder, rules.icons) as menu:
        found = members(menu)
        named = []
        for item in items:
            first = item.levels[0] if item.levels else item.id
            paths = (
                pattern.replace("{id}", str(item.id)).replace("{first}", str(first)).lower()
                for pattern in (patterns.get(item.kind, []) if item.id in shown else [])
            )
            source = next((path for path in paths if path in found), None)
            name = PurePosixPath(source).stem if source else ""
            if source and _ICON_NAME.fullmatch(name):
                if name not in icons:
                    try:
                        icons[name] = webp(menu.read(found[source]))
                    except ValueError as error:
                        raise ExtractionError(f"{source}: {error}") from None
                item = item.model_copy(update={"icon": name})
            named.append(item)
    return named, icons


def _dressed(heroes: HeroRules) -> set[int]:
    # The items whose parts the creator dresses its heroes in: what every hero of a faction holds,
    # the faction's hair styles and facial hair, and each class's starting outfit.
    dressed = set()
    for faction, classes in heroes.loadouts.items():
        rules = heroes.factions[faction]
        dressed |= {*rules.base_items, *rules.hair_styles, *rules.facial_hair}
        dressed |= {item for loadout in classes.values() for item in loadout.appearance}
    return dressed


def _executable(
    rules: CatalogueRules, file: Path, items: list[CatalogueItem]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    try:
        executable = Executable(file.read_bytes())
        table = executable.table_read_after(rules.daily_missions.after)
        daily = tuple(executable.int32s(table, rules.daily_missions.days))
        calls = executable.longest_call_run()
    except (OSError, ValueError) as error:
        raise ExtractionError(f"{file}: {error}") from None
    numbers = {item.id for item in items}
    if not set(daily) <= numbers:
        raise ExtractionError(f"{file} has no table of daily missions that the catalogue has")
    experience = tuple(xp for _, xp in calls if xp is not None)
    if (
        len(calls) != rules.experience.levels
        or len(experience) != len(calls)
        or any(level not in (None, index) for index, (level, _) in enumerate(calls))
        or list(experience[1:]) != sorted(set(experience[1:]))
    ):
        raise ExtractionError(f"{file} has no table of the XP of each level")
    return daily, experience
