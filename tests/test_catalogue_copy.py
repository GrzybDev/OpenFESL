from pathlib import Path
from zipfile import ZipFile

import pytest
from game_files import COPY, NEEDS_COPY

from openfesl.core.catalogue import CatalogueFile
from openfesl.core.games import load_games
from openfesl.extraction import extract, find_game
from openfesl.formats.definitions import read_definitions

pytestmark = NEEDS_COPY


@pytest.fixture(scope="module")
def catalogue(tmp_path_factory: pytest.TempPathFactory) -> CatalogueFile:
    assert COPY
    folder = Path(COPY)
    extracted = extract(find_game(load_games(), folder), folder)
    written = tmp_path_factory.mktemp("catalogue") / "bfheroes-catalogue.zip"
    written.write_bytes(extracted.write())
    return CatalogueFile.read(written.read_bytes())


def test_the_copy_gives_every_kind_of_item_with_names_and_icons(catalogue: CatalogueFile) -> None:
    document = catalogue.document
    items = {item.id: item for item in document.items}
    game = load_games().by_id("bfheroes")
    assert game and game.catalogue
    kinds = {item.kind for item in document.items}
    assert kinds == {item_file.kind for item_file in game.catalogue.items}
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
) -> None:
    document = catalogue.document
    daily = document.daily_missions
    assert len(daily) == 366
    assert (daily[0], daily[299], daily[365]) == (6127, 6123, 6041)
    rules = load_games().by_id("bfheroes")
    assert COPY and rules and rules.catalogue
    with ZipFile(Path(COPY) / rules.catalogue.definitions) as definitions:
        missions = read_definitions(definitions.read("Missions.xml"), "item")
    categories = {mission.id: mission.fields.get("category") for mission in missions}
    assert {categories[mission] for mission in daily} == {"DailyMission"}
    experience = document.experience
    assert len(experience) == 30
    assert (experience[1], experience[2], experience[29]) == (600, 1_200, 286_400)
