import os
import struct
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from PIL import Image

# A player's own copy of Battlefield Heroes, 1.89 or 1.46, which the opt-in tests only read.
COPY = os.environ.get("OPENFESL_TEST_BFHEROES_DIR")
NEEDS_COPY = pytest.mark.skipif(
    not COPY, reason="OPENFESL_TEST_BFHEROES_DIR names no copy of Battlefield Heroes"
)

# Where the invented executables' sections load.
BASE = 0x400000
CODE = BASE + 0x1000
DATA = BASE + 0x8000
_ALIGNMENT = 0x200

# The check that Battlefield Heroes' executable makes before it reads its daily missions.
DAILY_CHECK = b"adjustedDayInYear < 366\0"
# An invented copy of Battlefield Heroes: its daily missions alternate, and each level needs 100
# XP more than the one before.
DAILY = [6000 if day % 2 == 0 else 6001 for day in range(366)]
EXPERIENCE = [0, *(100 * level for level in range(1, 30))]
DEFINITIONS = {
    "Items.xml": """<database><items>
        <item id="1"><faction>1</faction><kits>7</kits><slot>0</slot><minLevel>1</minLevel>
          <template layer="1">invented_hat</template></item>
        <item id="2"><faction>2</faction><kits>2</kits><slot>11</slot><minLevel>1</minLevel></item>
        </items></database>""",
    "Weapons.xml": """<weapons><!-- invented_rifle --><item id="3000"><faction>3</faction>
        <kits>4</kits><class>1</class><values><range>3</range></values></item></weapons>""",
    "Abilities.xml": """<abilities>
        <item><faction>1</faction><kits>1</kits>
          <upgrade id="2000"><values><cost>1</cost></values></upgrade>
          <upgrade id="2001"><values><cost>1</cost></values></upgrade></item>
        <item><faction>3</faction><kits>7</kits><consumable>true</consumable>
          <upgrade id="2010"><values><cost>1</cost></values></upgrade></item>
        </abilities>""",
    "Emotes.xml": """<emotes><item id="5000"><faction>3</faction><trigger>wave</trigger></item>
        </emotes>""",
    "Boosters.xml": """<boosters><item id="9000"><values><mod>0.5</mod></values></item>
        </boosters>""",
    "Missions.xml": """﻿<missions>
        <item id="6000"><category>DailyMission</category><kits>9</kits><minLevel>0</minLevel>
          <teamPrereq>2</teamPrereq><criterias><criteria><val>1</val></criteria></criterias></item>
        <item id="6001"><category>DailyMission</category><kits>63</kits><minLevel>5</minLevel>
          <teamPrereq>0</teamPrereq></item></missions>""",
    "DefaultCustomization.xml": """<?xml version="1.0" encoding="utf-8" ?><customizations>
        <team id="1"><kit id="0"><appearance>2</appearance><equipment>3000;2000</equipment>
        </kit></team></customizations>""",
    "Colors.xml": '<colors><skin><c id="1">30 20 15</c></skin></colors>',
}
TEXTS = [
    "STRINGID\tENG_US\tPOL_PL\tCATEGORY",
    "WEST_Custom_Item_1\tInvented Hat\tWymyślony kapelusz\tSHARED",
    "WEST_Custom_Item_3000\tPractice Rifle\t\tSHARED",
    "WEST_Custom_Item_2000\tInvented Dash\tWymyślony zryw\tSHARED",
    "west_custom_item_5000 \tInvented Wave\tWymyślone machnięcie\tSHARED",
    "AUTO_RULES_MISSION_6000\tInvented Daily\tWymyślona codzienna\tSHARED",
    "AUTO_RULES_MISSION_6145\tInvented Challenge\tWymyślone wyzwanie\tSHARED",
    "WEST_Custom_Bundle_NewRoyalEmoteBundle\tInvented Emote Pair\tWymyślona para gestów\tSHARED",
    "WEST_Custom_Bundle_InventedLeftover\tInvented Leftover\tWymyślona resztka\tSHARED",
    "REWARDS_HALLOWEEN_DAY_3_ROYAL_NAME\tInvented Treat\tWymyślony smakołyk\tSHARED",
    "REWARDS_INVENTED_LEFTOVER\tInvented Gift\tWymyślony prezent\tSHARED",
    "WEST_Custom_Item_Info_1\tA hat with a feather.\tKapelusz z piórkiem.\tSHARED",
    "WEST_FE_WEAPON_DESCRIPTION_3001\tA rifle nobody made.\tKarabin, którego nie ma.\tSHARED",
    "WEST_FE_Invented_Title\tInvented Battles\tWymyślone bitwy\tMENU",
    "",
]
# Where the menu keeps each icon, in the case its makers wrote the names, and the format.
ICONS = {
    "External/FlashMenu/images/items/item1.dds": "DDS",
    "HUD/Texture/Ingame/Weapons/weapon3000.tga": "TGA",
    "External/FlashMenu/images/Items/Ability2000.tga": "TGA",
    "External/FlashMenu/images/items/citem2010.png": "PNG",
    "External/FlashMenu/images/items/emote5000.png": "PNG",
    "External/FlashMenu/images/items/mission6000.png": "PNG",
}


def invented_copy(folder: Path) -> None:
    """Write the files of an invented copy of Battlefield Heroes' catalogue into a folder."""
    mod = folder / "mods" / "bfheroes"
    (mod / "Objects").mkdir(parents=True)
    (mod / "Localization").mkdir()
    with ZipFile(mod / "Objects" / "Items_server.zip", "w") as definitions:
        for name, text in DEFINITIONS.items():
            definitions.writestr(name, text.encode())
    (mod / "Localization" / "strings.csv").write_bytes("\r\n".join(TEXTS).encode("utf-16"))
    with ZipFile(mod / "Menu_client.zip", "w") as menu:
        for number, (name, form) in enumerate(ICONS.items()):
            icon = BytesIO()
            Image.new("RGBA", (4, 4), (number * 40, 90, 200, 255)).save(icon, form)
            menu.writestr(name, icon.getvalue())
        menu.writestr("External/FlashMenu/mainMenu.txt", b"not an icon")
    padding = bytes(-len(DAILY_CHECK) % 4)
    data = DAILY_CHECK + padding + struct.pack(f"<{len(DAILY)}i", *DAILY)
    code = level_table_code(EXPERIENCE)
    code += table_read_code(DATA, DATA + len(DAILY_CHECK) + len(padding))
    (folder / "BFHeroes.exe").write_bytes(windows_executable(code, data))


def windows_executable(code: bytes, data: bytes) -> bytes:
    """Return a 32-bit Windows executable with a code section at CODE and a data one at DATA."""
    header = 0x40
    optional = bytearray(0xE0)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<I", optional, 28, BASE)
    code_at = _ALIGNMENT
    data_at = code_at + _aligned(len(code))
    sections = b"".join(
        struct.pack("<8s6I2HI", name, len(body), address - BASE, len(body), at, 0, 0, 0, 0, flags)
        for name, body, address, at, flags in (
            (b".text", code, CODE, code_at, 0x60000020),
            (b".data", data, DATA, data_at, 0xC0000040),
        )
    )
    image = bytearray(b"MZ" + bytes(header - 2))
    struct.pack_into("<I", image, 0x3C, header)
    image += b"PE\0\0" + struct.pack("<2H3I2H", 0x14C, 2, 0, 0, 0, len(optional), 0x102)
    image += optional + sections
    image += bytes(code_at - len(image)) + code
    image += bytes(data_at - len(image)) + data
    return bytes(image + bytes(_aligned(len(image)) - len(image)))


def push(value: int) -> bytes:
    """Return the instruction that pushes a constant, in its short form when it fits."""
    if -128 <= value < 128:
        return b"\x6a" + struct.pack("<b", value)
    return b"\x68" + struct.pack("<i", value)


def method_call(site: int, target: int) -> bytes:
    """Return a call of a method on the object in esi, made at an address of the code."""
    return b"\x8b\xce\xe8" + struct.pack("<i", target - (site + 7))


def level_table_code(experience: list[int], start: int = CODE) -> bytes:
    """Return code that sets each level's XP with one call, the level of 2 pushed from ebx."""
    code = bytearray(b"\x90" * 16)
    for level, xp in enumerate(experience):
        code += push(xp) + (b"\x53" if level == 2 else push(level))
        code += method_call(start + len(code), start)
    return bytes(code + b"\xc3")


def table_read_code(string: int, table: int) -> bytes:
    """Return code that pushes a string's address, then reads a table at index esi times four."""
    return (
        b"\x68"
        + struct.pack("<I", string)
        + b"\x90" * 9
        + b"\x8b\x04\xb5"
        + struct.pack("<I", table)
    )


def _aligned(size: int) -> int:
    return -(-size // _ALIGNMENT) * _ALIGNMENT
