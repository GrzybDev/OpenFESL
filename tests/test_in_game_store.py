import re
from pathlib import Path
from urllib.parse import urlencode

import pytest
from heroes import MAGMA, wallets
from store import CHEER, ITEMS, ROYAL_SOLDIER, grant, listed, offers, shopper, stock
from support import fetch, visible

pytestmark = pytest.mark.anyio

STORE = "/en/game/store"


async def opened(
    server_url: str, path: str, cookie: dict[str, str], form: dict[str, str] | None = None
) -> str:
    """Open a page of the in-game store as the game's browser does, or send one of its forms."""
    body = urlencode(form).encode() if form is not None else None
    headers = {**cookie, "Content-Type": "application/x-www-form-urlencoded"} if form else cookie
    _, _, page = await fetch(server_url, path, MAGMA, headers, body)
    return page.decode()


async def product(server_url: str, line: str) -> str:
    """Return the number of the product that the store lists in a line such as 3015-tlmd 1d."""
    found = [listed(entry) for entry in await offers(server_url)]
    return next(entry for entry in found if line in entry).split()[0].removeprefix("OFB-DBFH:")


@pytest.mark.usefixtures("prompt_actions")
async def test_the_game_store_shows_a_hero_what_fits_it_and_what_it_holds(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, [*ITEMS, CHEER])
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await grant(database_url, hero, "_DV", 600)
    # The game counts the bandages used in the round in its query; the rest of it is not read.
    query = f"?personaId={hero}&lvl=1&eqp=3011+2075+2005_3&slots=2005_1&kr=-1"

    store = visible((await opened(server_url, STORE + query, cookie)).encode())
    assert "Brassbuckle Royal Soldier · level 1" in store
    assert "2,500 Funds 600 Valor Points" in store
    assert "Items for Brassbuckle." in store
    # Only what fits a Royal soldier, without the abilities that Hero Points buy in the game.
    assert "Weapons 1 item · none owned" in store
    assert "Clothing 2 items · none owned" in store
    assert "Widgets 2 items · 1 owned · 1 running low" in store
    assert "Emotes 2 items · 1 owned" in store
    assert "Boosts 1 item · none owned" in store

    weapons = visible((await opened(server_url, f"{STORE}/weapons{query}", cookie)).encode())
    assert "Invented Rifle Not owned from 250 Valor Points 6,720 Funds" in weapons
    assert "Longrifle" not in weapons
    widgets = visible((await opened(server_url, f"{STORE}/widgets{query}", cookie)).encode())
    assert "Invented Bandage Equipped 3 uses left · running low" in widgets
    emotes = visible((await opened(server_url, f"{STORE}/emotes{query}", cookie)).encode())
    assert "Invented Cheer Equipped Permanent" in emotes

    # The first price point the account can pay is picked, and what buying it leaves is said.
    rifle = visible((await opened(server_url, f"{STORE}/weapons/3015{query}", cookie)).encode())
    assert "Invented Rifle Royal Soldier" in rifle
    assert "Brassbuckle · Not owned" in rifle
    assert "1 day 250 Valor Points" in rifle and "Permanent 6,720 Funds 4,220 short" in rifle
    assert "After buying: 1 day left, until" in rifle
    assert "the account keeps 350 Valor Points." in rifle
    for_good = await product(server_url, "3015-unlm")
    picked = f"{STORE}/weapons/3015{query}&pick={for_good}"
    short = visible((await opened(server_url, picked, cookie)).encode())
    assert "The account has 2,500 Funds; this costs 6,720." in short
    assert "Where Funds come from" in short

    need = f"{STORE}/weapons/3015/need{query}&product={for_good}"
    more = visible((await opened(server_url, need, cookie)).encode())
    assert "Need more Funds Not enough Funds Price 6,720 Funds Invented Rifle, permanent" in more
    assert "The account has 2,500 Funds Short 4,220 Funds" in more
    assert "How Funds are earned" in more
    assert "1 day costs 250 Valor Points, which the account has." in more

    cheer = visible((await opened(server_url, f"{STORE}/emotes/5016{query}", cookie)).encode())
    assert "Brassbuckle owns this permanently; there is nothing to buy." in cheer

    polish = visible((await opened(server_url, f"/pl/game/store{query}", cookie)).encode())
    assert "Przedmioty dla bohatera Brassbuckle." in polish
    assert "Bronie 1 przedmiot · nic w posiadaniu" in polish
    # The game names Brazilian Portuguese br, in which the coat has a name of its own.
    clothing = await opened(server_url, f"/br/game/store/clothing{query}", cookie)
    assert "Casaco inventado" in visible(clothing.encode())


@pytest.mark.usefixtures("base")
async def test_the_game_store_opens_only_for_a_hero_of_the_sessions_account(
    server_url: str,
) -> None:
    ann, _ = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    _, bob_hero = await shopper(server_url, "bob@example.test", "Copperpot", **ROYAL_SOLDIER)

    # The game shows its own offline page for a page titled so: no session, a game started
    # without one, an unknown one, another account's hero, no hero at all.
    refused = [
        await opened(server_url, f"{STORE}?personaId={bob_hero}", {}),
        await opened(server_url, f"{STORE}?personaId={bob_hero}", {"Cookie": "magma=0"}),
        await opened(server_url, f"{STORE}?personaId={bob_hero}", {"Cookie": "magma=forged"}),
        await opened(server_url, f"{STORE}?personaId={bob_hero}", ann),
        await opened(server_url, f"{STORE}?personaId=x", ann),
        await opened(server_url, f"/en/game/store/weapons/3015?personaId={bob_hero}", ann),
    ]
    for page in refused:
        assert "<title>offline</title>" in page
        assert "The store is offline" in visible(page.encode())


@pytest.mark.usefixtures("prompt_actions")
async def test_a_hero_buys_in_the_game_store_and_a_refusal_spends_nothing(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, [*ITEMS, CHEER])
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    bob, bob_hero = await shopper(server_url, "bob@example.test", "Copperpot", **ROYAL_SOLDIER)
    await grant(database_url, hero, "_DV", 600)
    rifle = f"{STORE}/weapons/3015?personaId={hero}"
    token = re.search(r'name="csrf" value="([^"]+)"', await opened(server_url, rifle, cookie))
    assert token is not None
    day = await product(server_url, "3015-tlmd 1d")
    for_good = await product(server_url, "3015-unlm")

    def form(product: str, price: int, csrf: str = token[1]) -> dict[str, str]:
        return {"csrf": csrf, "product": product, "price": str(price), "equip": "1"}

    # A form of an older page, a price that changed meanwhile and a price above the balance.
    expired = await opened(server_url, rifle, cookie, form(day, 250, "forged"))
    assert "Not bought The store's page had expired. Nothing was spent;" in visible(
        expired.encode()
    )
    changed = await opened(server_url, rifle, cookie, form(day, 200))
    assert "The price points of this item changed a moment ago." in visible(changed.encode())
    poor = visible((await opened(server_url, rifle, cookie, form(for_good, 6720))).encode())
    assert "Need more Funds" in poor and "Short 4,220 Funds" in poor
    # Nor is the cheer sold, which the game grants the hero.
    emote = f"{STORE}/emotes/5016?personaId={hero}"
    cheer = await product(server_url, "5016-unlm")
    owned = visible((await opened(server_url, emote, cookie, form(cheer, 1680))).encode())
    assert "Brassbuckle owns this item permanently by now. Nothing was spent." in owned
    # Nor may the account buy for another account's hero.
    theirs = f"{STORE}/weapons/3015?personaId={bob_hero}"
    assert "<title>offline</title>" in await opened(server_url, theirs, cookie, form(day, 250))
    assert await wallets(server_url, cookie, hero) == {"_DH": 0, "_DV": 600, "_PF": 2500}
    assert await wallets(server_url, bob, bob_hero) == {"_DH": 0, "_DV": 0, "_PF": 2500}

    bought = visible((await opened(server_url, rifle, cookie, form(day, 250))).encode())
    assert "Bought and equipped Invented Rifle, 1 day, for Brassbuckle." in bought
    assert "250 Valor Points spent; the account has 350 Valor Points." in bought
    assert "Brassbuckle now has 1 day left." in bought
    # The item the page tells the game of is the one the game then loads.
    _, _, held = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
    assert "<entitlementTag>3015-tlmd</entitlementTag>" in held.decode()

    # The game counts 7 of the hero's 10 bandages left in the round; 25 more make it 32.
    bandages = f"{STORE}/widgets/2005?personaId={hero}&eqp=2005_7"
    pack = await product(server_url, "2005-lmct x25")
    restocked = await opened(server_url, bandages, cookie, form(pack, 175) | {"equip": "0"})
    assert "Brassbuckle now has 32 uses left." in visible(restocked.encode())
    # The last item named of a slot is the one worn there, as buying and equipping names the
    # bought one last: the coat, then the partner's shirt of the same slot, and the other way.
    clothing = [
        visible((await opened(server_url, f"{STORE}/clothing{query}", cookie)).encode())
        for query in (f"?personaId={hero}&eqp=3+1468", f"?personaId={hero}&eqp=1468+3")
    ]
    assert "Invented Coat Not owned" in clothing[0]
    assert "Invented Coat Equipped" in clothing[1]
