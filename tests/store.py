"""Helpers of the tests of the stores, which sell the items of imported catalogues."""

from pathlib import Path
from xml.etree import ElementTree

import anyio
import pytest
from heroes import MAGMA
from support import cli, fetch

from openfesl.core.catalogue import CatalogueDocument, CatalogueFile, CatalogueItem


def item(number: int, kind: str, **fields: str) -> CatalogueItem:
    """Return an item of an invented catalogue that any hero may use unless its fields say."""
    return CatalogueItem(id=number, kind=kind, fields={"faction": "3", "kits": "7", **fields})


# The levels of an ability, each an item of its own.
DASH = (2096, 2097, 2098)
# An invented Battlefield Heroes catalogue: a Royal base hat, a hair style, a Royal and a National
# coat, a shirt that the game data withholds, a pet, a partner's shirt, a bandage, three levels of
# an ability, a tonic, a loadout knife, a Royal soldier's rifle, a National commando's rifle, a
# weapon without a name, an emote, a mission and a boost.
ITEMS = [
    item(1, "appearance", faction="1", slot="0", default="True"),
    item(2, "appearance", slot="11"),
    item(3, "appearance", faction="1", slot="3"),
    item(4, "appearance", faction="2", slot="3"),
    item(257, "appearance", faction="1", slot="3"),
    item(521, "appearance", faction="1", slot="8"),
    item(1468, "appearance", slot="3"),
    item(2005, "ability", consumable="true"),
    *(item(level, "ability").model_copy(update={"levels": DASH}) for level in DASH),
    item(2153, "ability", consumable="true"),
    item(3001, "weapon", faction="1", kits="1"),
    item(3015, "weapon", faction="1", kits="2"),
    item(3042, "weapon", faction="2", kits="1"),
    item(3050, "weapon"),
    item(5001, "emote"),
    CatalogueItem(id=6000, kind="mission", fields={"teamPrereq": "0", "kits": "63"}),
    item(9000, "convenience"),
]
NAMES = {
    1: "Invented Hat",
    2: "Invented Quiff",
    3: "Invented Coat",
    4: "Invented Tunic",
    257: "Invented Logo Shirt",
    521: "Invented Monkey",
    1468: "Invented Partner Shirt",
    2005: "Invented Bandage",
    2096: "Invented Dash",
    2153: "Invented Tonic",
    3001: "Invented Knife",
    3015: "Invented Rifle",
    3042: "Invented Longrifle",
    5001: "Invented Wave",
    9000: "Invented Boost",
}


def catalogue_file(folder: Path, items: list[CatalogueItem] = ITEMS) -> Path:
    """Write the file of an invented catalogue as `openfesl catalogue extract` writes it."""
    texts = {f"WEST_Custom_Item_{number}": name for number, name in NAMES.items()}
    texts["AUTO_RULES_MISSION_6000"] = "Invented Mission"
    document = CatalogueDocument(format=1, game="bfheroes", items=tuple(items))
    file = folder / "catalogue.zip"
    file.write_bytes(CatalogueFile(document, {"en": texts}, {}).write())
    return file


async def offers(server_url: str) -> list[ElementTree.Element]:
    """Return the products of the store's offers, as the game loads them."""
    _, _, body = await fetch(server_url, "/ofb/products", MAGMA)
    return list(ElementTree.fromstring(body))


async def stock(server_url: str, folder: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Import the invented catalogue as the admin does, and wait for the store to fill from it."""
    await cli(capsys, "catalogue", "import", str(catalogue_file(folder)))
    with anyio.fail_after(30):
        while not await offers(server_url):
            await anyio.sleep(0.05)


def listed(product: ElementTree.Element) -> str:
    """Return a product's id, tag, rental days or uses, and price, as one line."""
    attributes = {
        attribute.get("name", ""): attribute.get("value", "")
        for attribute in product.iter("attribute")
    }
    point = product.find("pricePoints/pricePoint")
    assert point is not None
    extra = "".join(
        f" {prefix}{attributes[name]}{suffix}"
        for name, prefix, suffix in (("duration", "", "d"), ("useCount", "x", ""))
        if name in attributes
    )
    return (
        f"{product.get('productId')} {attributes['entitlementTag']}{extra}"
        f" {point.get('price')}{point.get('currency')}"
    )
