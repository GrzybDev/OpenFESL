import json
import struct
from hashlib import sha256
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from game_files import DAILY, EXPERIENCE, invented_copy
from support import cli

from openfesl.core.catalogue import CatalogueFile
from openfesl.core.games import load_games
from openfesl.core.hero_models import Rig, Template
from openfesl.extraction.catalogue import extract, find_game
from openfesl.extraction.hero_models import extract_models
from openfesl.formats.refractor_scripts import MeshRule

pytestmark = pytest.mark.anyio


def fingerprint(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*")
        if path.is_file()
    }


async def test_a_copy_of_the_game_gives_a_file_of_its_items_texts_icons_and_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    copy, out = tmp_path / "Battlefield Heroes", tmp_path / "catalogue.zip"
    invented_copy(copy)
    before = fingerprint(copy)

    printed = await cli(capsys, "catalogue", "extract", str(copy), "-o", str(out))

    assert printed == (
        f"Wrote the catalogue of Battlefield Heroes to {out}: 12 items, texts in 2 languages, "
        "5 icons and 4 hero models with 2 textures\n"
    )
    assert fingerprint(copy) == before
    written = out.read_bytes()
    extracted = CatalogueFile.read(written)
    assert extracted.write() == written
    document = extracted.document
    assert document.game == "bfheroes"
    # Each file's items in the file's order; each level of an ability is an item. Only the items
    # that the site may show have icons: the unnamed tonic is never sold, so its icon stays out.
    assert [(item.id, item.kind, item.levels, item.icon) for item in document.items] == [
        (1, "appearance", (), "item1"),
        (2, "appearance", (), None),
        (3000, "weapon", (), "weapon3000"),
        (2000, "ability", (2000, 2001), "ability2000"),
        (2001, "ability", (2000, 2001), "ability2000"),
        (2010, "ability", (2010,), None),
        (5000, "emote", (), "emote5000"),
        (9000, "convenience", (), None),
        (6000, "mission", (), "mission6000"),
        (6001, "mission", (), None),
        (6002, "mission", (), None),
        (6003, "mission", (), None),
    ]
    # Only the fields that the game's rules read.
    hat, rifle, tonic = document.items[0], document.items[2], document.items[5]
    assert hat.fields == {
        "faction": "1",
        "kits": "7",
        "slot": "0",
        "minLevel": "1",
        "default": "True",
    }
    assert rifle.fields == {"faction": "3", "kits": "4"}
    assert tonic.fields == {"faction": "3", "kits": "7", "consumable": "true"}
    assert set(extracted.icons) == {item.icon for item in document.items if item.icon}
    assert all(icon[8:12] == b"WEBP" for icon in extracted.icons.values())
    assert document.tables["Colors.xml"].children[0].children[0].text == "30 20 15"
    assert document.tables["DefaultCustomization.xml"].tag == "customizations"
    # Only the texts that the game's data names, of items the catalogue holds, and those that
    # heroes' profiles show.
    assert extracted.texts["pl"] == {
        "WEST_Custom_Item_1": "Wymyślony kapelusz",
        "WEST_Custom_Item_2000": "Wymyślony zryw",
        "west_custom_item_5000 ": "Wymyślone machnięcie",
        "AUTO_RULES_MISSION_6000": "Wymyślona codzienna",
        "WEST_Custom_Bundle_NewRoyalEmoteBundle": "Wymyślona para gestów",
        "REWARDS_HALLOWEEN_DAY_3_ROYAL_NAME": "Wymyślony smakołyk",
        "WEST_Custom_Item_Info_1": "Kapelusz z piórkiem.",
        "WEST_LEVELNAME_LAKE": "Wymyślona zatoka",
        "HEROIC_MOMENT_HEADSHOT": "Wymyślony strzał",
        "Player_Prestige_Level_9": "[Wymyślony pułkownik ]",
    }
    assert extracted.texts["en"]["WEST_Custom_Item_3000"] == "Practice Rifle"
    assert document.daily_missions == tuple(DAILY)
    assert document.experience == tuple(EXPERIENCE)


async def test_a_folder_without_a_copy_or_with_a_part_missing_gives_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit, match="no copy of a game"):
        await cli(capsys, "catalogue", "extract", str(tmp_path))

    invented_copy(tmp_path)
    (tmp_path / "mods" / "bfheroes" / "Localization" / "strings.csv").unlink()
    with pytest.raises(SystemExit, match=r"strings\.csv cannot be read"):
        await cli(capsys, "catalogue", "extract", str(tmp_path))


def gltf(data: bytes) -> dict[str, Any]:
    length = struct.unpack_from("<I", data, 12)[0]
    document: dict[str, Any] = json.loads(data[20 : 20 + length])
    return document


def test_a_copy_gives_the_parts_and_animations_that_its_menu_draws_heroes_with(
    tmp_path: Path,
) -> None:
    invented_copy(tmp_path)

    catalogue = extract(find_game(load_games(), tmp_path), tmp_path)

    models = catalogue.document.models
    assert models is not None
    # Only the parts that items name, or that a rule puts in their place, with a script, a
    # region and their file; never a female hero's. Of the hair's two scripts, the later counts.
    assert models.templates == {
        "invented_hat": Template(type="cit_hat", kind="mesh"),
        "invented_cap": Template(type="cit_hat", kind="mesh"),
        "invented_hair": Template(
            type="cit_headhair", kind="texture", offset=(2, 3), colour=(30, 15, 10)
        ),
    }
    # The hair's item sets the layer of all its parts, as older copies do.
    assert models.layers == {
        1: ((1, "invented_hat"),),
        2: tuple(
            (55, name)
            for name in ("invented_hair", "invented_tail", "invented_lost", "invented_unwritten")
        ),
    }
    assert models.regions == {
        "cit_hat": (0, 808, 317, 216),
        "cit_headhair": (369, 512, 463, 296),
    }
    assert models.rules == (MeshRule(("invented_hat",), (), "invented_cap", ("invented_hair",)),)
    # The parts of the items that the creator never dresses stay out, and so do the rules that
    # only those start.
    game = find_game(load_games(), tmp_path)
    assert game.catalogue
    hat_alone, files, _ = extract_models(game.catalogue, tmp_path, catalogue.document.items, {1})
    assert set(hat_alone.templates) == {"invented_hat"} and hat_alone.rules == ()
    assert set(files) == {"invented_hat", "british_male_soldier", "german_male_soldier"}
    assert set(catalogue.models) == {
        "invented_hat",
        "invented_cap",
        "british_male_soldier",
        "german_male_soldier",
    }
    assert gltf(catalogue.models["invented_hat"])["meshes"][0]["name"] == "invented_hat"
    # The parts' textures are lossy, the menu's shading ramp exact.
    assert set(catalogue.textures) == {"invented_hair", models.warp}
    assert b"VP8 " in catalogue.textures["invented_hair"]
    assert b"VP8L" in catalogue.textures[models.warp]

    # Each faction's skeleton holds the clips of each role, and the National poses come from
    # where the copy keeps them.
    for faction, soldier in (
        ("royal", "british_male_soldier"),
        ("national", "german_male_soldier"),
    ):
        assert models.rigs[faction] == Rig(
            model=soldier,
            roles={
                "idle": ("idle",),
                "faces": ("face_idle1",),
                "poses": ("hands", "default"),
                "nod": ("yes",),
                "flex": ("flex",),
                "wave": ("wave",),
                "cheer": ("cheer",),
            },
        )
        animations = gltf(catalogue.models[soldier])["animations"]
        assert [clip["name"] for clip in animations] == [
            "idle",
            "face_idle1",
            "hands",
            "default",
            "yes",
            "flex",
            "wave",
            "cheer",
        ]


async def test_a_copy_without_a_file_of_its_models_gives_no_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invented_copy(tmp_path)
    soldiers = tmp_path / "mods" / "bfheroes" / "Objects" / "Soldiers_server.zip"
    lost = "german_male_soldier/Animations/Common/face_idle1.baf"
    with ZipFile(soldiers) as archive:
        kept = {name: archive.read(name) for name in archive.namelist() if name != lost}
    with ZipFile(soldiers, "w") as archive:
        for name, data in kept.items():
            archive.writestr(name, data)

    with pytest.raises(SystemExit, match=f"the copy lacks Objects/Soldiers/{lost}"):
        await cli(capsys, "catalogue", "extract", str(tmp_path))
