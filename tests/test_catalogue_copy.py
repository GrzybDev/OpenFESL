from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from game_files import COPY, NEEDS_COPY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.catalogue import CatalogueFile
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, load_games
from openfesl.core.hero_items import fits
from openfesl.extraction import extract, find_game
from openfesl.formats.definitions import read_definitions
from openfesl.storage.catalogues import IconFolder, SqlCatalogueStore
from openfesl.storage.database import migrate

pytestmark = NEEDS_COPY


@pytest.fixture(scope="module")
def written(tmp_path_factory: pytest.TempPathFactory) -> Path:
    assert COPY
    folder = Path(COPY)
    file = tmp_path_factory.mktemp("catalogue") / "bfheroes-catalogue.zip"
    file.write_bytes(extract(find_game(load_games(), folder), folder).write())
    return file


@pytest.fixture(scope="module")
def catalogue(written: Path) -> CatalogueFile:
    return CatalogueFile.read(written.read_bytes())


def test_the_copy_gives_every_kind_of_item_with_names_and_icons(
    catalogue: CatalogueFile, bfheroes: Game
) -> None:
    document = catalogue.document
    items = {item.id: item for item in document.items}
    rules = bfheroes.catalogue
    assert rules is not None
    assert {item.kind for item in document.items} == {item_file.kind for item_file in rules.items}
    # A National Soldier's SMG, and the last of the five levels of the bandage, with only the
    # fields that the game's rules read.
    smg = items[3169]
    assert smg.kind == "weapon"
    assert (smg.fields["faction"], smg.fields["kits"]) == ("2", "2") and "class" not in smg.fields
    bandage = items[2005]
    assert bandage.kind == "ability" and bandage.levels == (2001, 2002, 2003, 2004, 2005)
    assert all(item.id in item.levels for item in document.items if item.levels)
    # Only the texts that the site and Magma show, not the menus' own.
    assert catalogue.texts["en"]["WEST_Custom_Item_3169"]
    assert not [name for name in catalogue.texts["en"] if name.startswith("WEST_BACKEND_")]
    with_icons = [item for item in document.items if item.icon]
    assert len(with_icons) > 0.95 * len(document.items)
    assert {item.icon for item in with_icons} == set(catalogue.icons)
    assert all(icon[8:12] == b"WEBP" for icon in catalogue.icons.values())
    assert set(document.tables) == {"DefaultCustomization.xml", "Colors.xml"}


def test_the_copy_gives_the_daily_missions_and_the_xp_of_each_level(
    catalogue: CatalogueFile,
    bfheroes: Game,
) -> None:
    document = catalogue.document
    daily = document.daily_missions
    assert len(daily) == 366
    assert (daily[0], daily[299], daily[365]) == (6127, 6123, 6041)
    # The rotation lists every daily mission, which is how the server tells them apart.
    rules = bfheroes.catalogue
    assert COPY and rules
    with ZipFile(Path(COPY) / rules.definitions) as definitions:
        missions = read_definitions(definitions.read("Missions.xml"), "item")
    assert set(daily) == {
        mission.id for mission in missions if mission.fields.get("category") == "DailyMission"
    }
    experience = document.experience
    assert len(experience) == 30
    assert (experience[1], experience[2], experience[29]) == (600, 1_200, 286_400)


@pytest.mark.anyio
async def test_the_server_reads_the_copys_catalogue_as_the_game_does(
    written: Path, database_url: str, tmp_path: Path, bfheroes: Game
) -> None:
    engine = create_async_engine(database_url)
    await migrate(engine)
    catalogues = Catalogues(
        SqlCatalogueStore(async_sessionmaker(engine)), IconFolder(tmp_path), load_games()
    )
    try:
        await catalogues.import_file(written.read_bytes(), admin=None)
        english = await catalogues.of(bfheroes, "en")
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE catalogue, admin_action CASCADE"))
        await engine.dispose()

    smg, head = english.item(3169), english.item(1)
    assert smg and head
    assert (smg.kind, smg.slot, smg.factions, smg.classes) == (
        "weapon",
        None,
        frozenset({"national"}),
        frozenset({"soldier"}),
    )
    assert head.slot == "head" and head.factions == {"royal"}
    assert english.name(3169) and english.name(2004)
    assert english.daily_mission(date(2026, 1, 1)) == 6127
    assert english.name(6127)
    assert english.xp_to_next(1) == 600 and english.xp_to_next(30) is None
    [kept] = (tmp_path / "catalogues" / "bfheroes").iterdir()
    assert len(list(kept.iterdir())) == english.contents.icons
    # What a new hero's game writes of its loadout fits it, and its first mission comes with
    # level 1 and needs no other first, so the server takes the game's own writes of a new hero.
    # Without the catalogue, the server holds the base items that it marks for each faction.
    rules = bfheroes.heroes
    assert rules
    chained = english.item(6001)
    assert chained and chained.prerequisite == 6000
    for faction, loadouts in rules.loadouts.items():
        assert set(rules.factions[faction].base_items) == {
            item.id
            for item in english.contents.items.values()
            if item.base and faction in item.factions
        }
        for hero_class, loadout in loadouts.items():
            for key, numbers in (
                (rules.stats.appearance, loadout.appearance),
                (rules.stats.equipment, loadout.equipment),
                (rules.stats.emotes, loadout.emotes),
                ("c_wmid0", loadout.missions),
            ):
                stat = rules.written_stat(key)
                found = [english.item(number) for number in numbers]
                assert stat and all(
                    item and fits(item, stat.items, faction, hero_class, 1) for item in found
                ), (faction, hero_class, key)
            missions = [english.item(number) for number in loadout.missions]
            assert missions and all(item and item.prerequisite is None for item in missions)
