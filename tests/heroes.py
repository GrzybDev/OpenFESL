"""Helpers of the tests of heroes, which games such as Battlefield Heroes make on their site."""

import re
from xml.etree import ElementTree

import httpx2
from accounts import PASSWORD, post_sign_in
from identity import form_token
from support import FESL, THEATER, Game, enter_theater, fetch, launcher_sign_in

CREATOR = "/bfheroes/user/createHero"
MAGMA = "www.battlefieldheroes.com"


def look(**chosen: str) -> dict[str, str]:
    """Return the creator's fields for a National Army commando, with any of them changed."""
    return {
        "faction": "national",
        "class": "commando",
        "skin_tone": "8",
        "hair_colour": "2",
        "hair_style": "123",
        "facial_hair": "129",
        **chosen,
    }


async def create(
    client: httpx2.AsyncClient, base: str, name: str, **chosen: str
) -> httpx2.Response:
    """Send the creator's form of a signed-in client, as its naming step sends it."""
    page = await client.get(f"{base}{CREATOR}")
    form = {"csrf": form_token(page.text), "name": name, **look(**chosen)}
    return await client.post(f"{base}{CREATOR}", data=form)


async def create_hero(base: str, address: str, name: str, **chosen: str) -> int:
    """Sign an account in on the site and create a hero with it; return the hero's number."""
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, address, PASSWORD)
        created = await create(client, base, name, **chosen)
    assert created.status_code == 303, created.text
    return int(created.headers["location"].split("/")[3])


def pictures(page: str) -> list[str]:
    """Return what each hero picture on a page shows: a portrait's address or a glyph's name."""
    shown = []
    for spot in re.findall(r'class="hero-picture[^"]*"[^>]*>(.*?)</span>', page, re.DOTALL):
        found = re.search(r'src="([^"]+)"|#([\w-]+)"', spot)
        shown.append((found.group(1) or found.group(2)) if found else "")
    return shown


async def start(
    server_url: str, process: Game, account: str, role: str = "play"
) -> tuple[dict[str, str], int, int]:
    """Sign an account in at the launcher and log its game in to FESL and Theater as its hero.

    Args:
        server_url: The server's relay address.
        process: The game process.
        account: The account's login name.
        role: Whether the game is started to play or to host.

    Returns:
        The magma cookie the game sends, the account's number and the hero's.
    """
    session = (await launcher_sign_in(server_url, account, PASSWORD, role=role)).json()
    cookie = {"Cookie": f"magma={session['sessionId']}"}
    _, _, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
    token = ElementTree.fromstring(body).findtext("token") or ""
    await process.hello(FESL, "bfwest-pc", client_type="client-noreg")
    silent = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
    account_id = int((await process.request(FESL, "acct", silent))["userId"])
    hero = [("TXN", "NuLoginPersona"), ("name", session["personaName"])]
    playing = await process.request(FESL, "acct", hero)
    await enter_theater(process, THEATER, "bfwest-pc", playing["lkey"])
    return cookie, account_id, int(playing["userId"])


async def wallets(server_url: str, cookie: dict[str, str], hero: int) -> dict[str, int]:
    """Return the balances in a hero's wallets, by currency, as the game loads them."""
    _, _, body = await fetch(server_url, f"/nucleus/wallets/{hero}", MAGMA, cookie)
    answer = ElementTree.fromstring(body)
    assert answer.tag == "billingAccounts"
    return {
        wallet.findtext("currency") or "": int(wallet.findtext("balance") or "")
        for wallet in answer.iter("walletAccount")
    }
