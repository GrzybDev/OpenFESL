from hashlib import sha256
from pathlib import Path

import pytest
from game_files import DAILY, EXPERIENCE, invented_copy
from support import cli

from openfesl.core.catalogue import CatalogueFile

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
        f"Wrote the catalogue of Battlefield Heroes to {out}: 12 items, texts in 2 languages "
        "and 6 icons\n"
    )
    assert fingerprint(copy) == before
    written = out.read_bytes()
    extracted = CatalogueFile.read(written)
    assert extracted.write() == written
    document = extracted.document
    assert document.game == "bfheroes"
    # Each file's items in the file's order; each level of an ability is an item.
    assert [(item.id, item.kind, item.levels, item.icon) for item in document.items] == [
        (1, "appearance", (), "item1"),
        (2, "appearance", (), None),
        (3000, "weapon", (), "weapon3000"),
        (2000, "ability", (2000, 2001), "ability2000"),
        (2001, "ability", (2000, 2001), "ability2000"),
        (2010, "ability", (2010,), "citem2010"),
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
    assert tonic.fields == {"faction": "3", "kits": "7"}
    assert set(extracted.icons) == {item.icon for item in document.items if item.icon}
    assert all(icon[8:12] == b"WEBP" for icon in extracted.icons.values())
    assert document.tables["Colors.xml"].children[0].children[0].text == "30 20 15"
    assert document.tables["DefaultCustomization.xml"].tag == "customizations"
    # Only the texts that the game's data names, of items the catalogue holds.
    assert extracted.texts["pl"] == {
        "WEST_Custom_Item_1": "Wymyślony kapelusz",
        "WEST_Custom_Item_2000": "Wymyślony zryw",
        "west_custom_item_5000 ": "Wymyślone machnięcie",
        "AUTO_RULES_MISSION_6000": "Wymyślona codzienna",
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
