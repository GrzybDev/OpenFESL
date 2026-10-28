"""Helpers of the tests of the stores, which sell the items of imported catalogues."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from xml.etree import ElementTree

import anyio
import pytest
from accounts import PASSWORD, execute
from heroes import MAGMA, create_hero, start
from support import FESL, THEATER, cli, fetch, game, launcher_sign_in, let_in, log_in_player
from test_player_servers import SERVER_GAME

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
# An emote of every loadout, which the game grants each hero itself.
CHEER = item(5016, "emote")
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
    5016: "Invented Cheer",
    9000: "Invented Boost",
}


# A bundle's name and description in English and Polish.
BUNDLE_TEXTS = {
    "WEST_Custom_Bundle_Invented": ("Invented Set", "Wymyślony zestaw"),
    "WEST_Custom_Bundle_Invented_HotDealsDesc": (
        "A coat, a pet, bandages.",
        "Płaszcz, zwierzak, bandaże.",
    ),
}
# The Royal coat's and a bundle's names in Brazilian Portuguese.
BRAZILIAN = {
    "WEST_Custom_Item_3": "Casaco inventado",
    "WEST_Custom_Bundle_Invented": "Conjunto inventado",
}


def catalogue_file(folder: Path, items: list[CatalogueItem] = ITEMS) -> Path:
    """Write the file of an invented catalogue as `openfesl catalogue extract` writes it."""
    english = {f"WEST_Custom_Item_{number}": name for number, name in NAMES.items()}
    english["AUTO_RULES_MISSION_6000"] = "Invented Mission"
    english |= {name: texts[0] for name, texts in BUNDLE_TEXTS.items()}
    polish = {name: texts[1] for name, texts in BUNDLE_TEXTS.items()}
    document = CatalogueDocument(format=1, game="bfheroes", items=tuple(items))
    file = folder / "catalogue.zip"
    texts = {"en": english, "pl": polish, "pt-BR": BRAZILIAN}
    file.write_bytes(CatalogueFile(document, texts, {}).write())
    return file


async def offers(server_url: str) -> list[ElementTree.Element]:
    """Return the products of the store's offers, as the game loads them."""
    _, _, body = await fetch(server_url, "/ofb/products", MAGMA)
    return list(ElementTree.fromstring(body))


async def items(server_url: str, cookie: dict[str, str], hero: int) -> list[str]:
    """Return a hero's items as the game loads them, each as its tag and uses."""
    _, _, body = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
    return [
        f"{entry.findtext('entitlementTag')} {entry.findtext('useCount')}"
        for entry in ElementTree.fromstring(body)
    ]


async def deals(server_url: str, language: str) -> list[ElementTree.Element]:
    """Return the hot deals as the game loads them in a language."""
    _, _, body = await fetch(server_url, f"/{language}/game/hotDeals", MAGMA)
    return list(ElementTree.fromstring(body))


def attribute(product: ElementTree.Element, name: str) -> str | None:
    """Return a product's attribute as the game loads it; none where it lacks it."""
    found = product.find(f"attributes/attribute[@name='{name}']")
    return found.get("value") if found is not None else None


async def stock(
    server_url: str,
    folder: Path,
    capsys: pytest.CaptureFixture[str],
    items: list[CatalogueItem] = ITEMS,
) -> None:
    """Import the invented catalogue as the admin does, and wait for the store to fill from it."""
    await cli(capsys, "catalogue", "import", str(catalogue_file(folder, items)))
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


# The creator's choices of a Royal Army soldier, whom the invented rifle fits.
ROYAL_SOLDIER = {"faction": "royal", "class": "soldier"}


async def shopper(
    server_url: str, account: str, name: str, **look: str
) -> tuple[dict[str, str], int]:
    """Create a hero on the site and sign its account in at the launcher.

    Returns:
        The magma cookie the game sends, and the hero's number.
    """
    hero = await create_hero(
        server_url.replace("ws://", "http://").rstrip("/"), account, name, **look
    )
    session = (await launcher_sign_in(server_url, account, PASSWORD)).json()
    return {"Cookie": f"magma={session['sessionId']}"}, hero


async def buy(
    server_url: str, cookie: dict[str, str], hero: int, product: int | str
) -> tuple[str, str | None]:
    """Buy a product by its number for a hero as the game does, with a POST without a body.

    Returns:
        The answer's root, and the cause of its failure, if it is one.
    """
    path = f"/ofb/purchase/{hero}/OFB-DBFH:{product}"
    answer = ElementTree.fromstring((await fetch(server_url, path, MAGMA, cookie, b""))[2])
    failure = answer.find("failure")
    return answer.tag, failure.get("cause") if failure is not None else None


async def grant(database_url: str, hero: int, currency: str, amount: int) -> None:
    """Credit a hero's wallet of a currency, its account's for a currency the heroes share."""
    await execute(
        database_url,
        "WITH changed AS (INSERT INTO wallet AS kept (account_id, game, currency, persona_id,"
        " balance) SELECT account_id, 'bfheroes', CAST(:currency AS text),"
        " CASE WHEN CAST(:currency AS text) = '_DH' THEN id END, CAST(:amount AS bigint)"
        " FROM persona WHERE id = :hero ON CONFLICT (account_id, game, currency, persona_id)"
        " DO UPDATE SET balance = kept.balance + :amount RETURNING id, balance)"
        " INSERT INTO wallet_entry (wallet_id, delta, balance, reason)"
        " SELECT id, :amount, balance, 'admin' FROM changed",
        hero=hero,
        currency=currency,
        amount=amount,
    )


async def without_rotation(database_url: str) -> None:
    """Switch the hot deals' rotation off as the admin does, so that only added bundles are sold."""
    await execute(
        database_url, "INSERT INTO setting (key, value) VALUES ('bfheroes.deals.enabled', 'off')"
    )


@asynccontextmanager
async def kept_by_server(server_url: str) -> AsyncIterator[tuple[dict[str, str], int]]:
    """Have a dedicated server keep the data of Ann's Royal soldier while it plays in its game.

    Hosting must be open, so that the server's game is listed at once.

    Yields:
        The server's magma cookie and Ann's hero.
    """
    for account in ("ann@example.test", "host@example.test"):
        async with game(server_url) as registering:
            await log_in_player(registering, FESL, account)
        await shopper(server_url, account, account.split("@")[0], **ROYAL_SOLDIER)
    async with game(server_url) as ann, game(server_url) as server:
        _, _, hero = await start(server_url, ann, "ann@example.test")
        cookie, _, _ = await start(server_url, server, "host@example.test", role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        await let_in(server, ann, created.fields)
        yield cookie, hero
