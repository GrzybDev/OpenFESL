from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from game_files import COPY, NEEDS_COPY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.catalogue import CatalogueFile
from openfesl.core.catalogues import Catalogues
from openfesl.core.figures import Figures
from openfesl.core.games import Game, load_games
from openfesl.core.hero_items import fits
from openfesl.core.heroes import Look
from openfesl.extraction.catalogue import extract, find_game
from openfesl.formats.definitions import read_definitions
from openfesl.storage.catalogues import CatalogueFolder, SqlCatalogueStore

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
    assert bandage.fields["consumable"] == "true"
    assert all(item.id in item.levels for item in document.items if item.levels)
    # Only the texts that the site and Magma show, not the menus' own.
    assert catalogue.texts["en"]["WEST_Custom_Item_3169"]
    assert not [name for name in catalogue.texts["en"] if name.startswith("WEST_BACKEND_")]
    # The icons of what the site may show: the withheld shirts keep theirs for the admin panel,
    # while the partners' medals, which nothing gives, have none.
    with_icons = [item for item in document.items if item.icon]
    assert len(with_icons) > 0.8 * len(document.items)
    assert all(items[number].icon for number in (257, 264, 284, 291))
    assert items[450].icon is None and items[928].icon is None
    assert {item.icon for item in with_icons} == set(catalogue.icons)
    assert all(icon[8:12] == b"WEBP" for icon in catalogue.icons.values())
    assert set(document.tables) == {"DefaultCustomization.xml", "Colors.xml"}


def test_the_copy_gives_the_models_of_every_heros_choices_in_the_creator(
    catalogue: CatalogueFile, written: Path, bfheroes: Game
) -> None:
    models = catalogue.document.models
    rules = bfheroes.heroes
    assert models and rules
    dressed: set[int] = set()
    for faction, figure in rules.factions.items():
        rig = models.rigs[faction]
        assert rig.model in catalogue.models
        roles = ("idle", "faces", "poses", "nod", "flex", "wave", "cheer")
        assert all(rig.roles[role] for role in roles)
        # The parts of each hair style, facial hair and starting outfit are all there.
        outfits = [item for found in rules.loadouts[faction].values() for item in found.appearance]
        for item in (*figure.hair_styles, *figure.facial_hair, *outfits):
            assert models.layers[item], item
            assert all(name in models.templates for _, name in models.layers[item]), item
        dressed |= {*figure.base_items, *figure.hair_styles, *figure.facial_hair, *outfits}
    for name, template in models.templates.items():
        assert template.type in models.regions
        assert name in (catalogue.models if template.kind == "mesh" else catalogue.textures)
    assert models.warp in catalogue.textures
    # Only the creator's parts, which keep the file at about 9 MiB for 1.89.
    assert set(models.layers) <= dressed
    assert written.stat().st_size < 12 * 2**20


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
    written: Path,
    catalogue_sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    bfheroes: Game,
) -> None:
    catalogues = Catalogues(
        SqlCatalogueStore(catalogue_sessions), CatalogueFolder(tmp_path), load_games()
    )
    await catalogues.import_file(written.read_bytes(), admin=None)
    english = await catalogues.of(bfheroes, "en")
    # The hero of the portrait that the game uploads (mugshot.png): a National commando in its
    # starting outfit with the seventh skin tone, which the models drew alike in the render
    # trial.
    portrait = Figures(catalogues).figure(bfheroes, Look("national", "commando", 7, 1, 0, 0))

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
    contents = english.contents
    assert len(list((kept / "icons").iterdir())) == contents.icons
    assert len(list((kept / "models").iterdir())) == contents.models
    assert len(list((kept / "textures").iterdir())) == contents.textures
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
    assert portrait and portrait.parts == (
        "ger_m_hands001",
        "ger_m_head003",
        "ger_m_feet002",
        "ger_m_legs006",
        "ger_m_body002",
    )
    assert [layer.texture for layer in portrait.layers] == [
        "ger_m_hands001_hands001",
        "ger_m_body001_body001",
        "ger_m_legs001_legs001",
        "ger_m_feet001_feet001",
        "ger_m_head001_head001",
        "ger_m_head_wrincle005",
        "ger_m_head_moutheye001",
        "ger_m_legs001_underwear001",
        "ger_m_feet002_boots001",
        "ger_m_legs006_trousers012",
        "ger_m_body002_jacket009",
    ]
    assert [layer.tint for layer in portrait.layers[5:7]] == [(153, 119, 86), None]
