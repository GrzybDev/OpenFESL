import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from heroes import create_hero
from store import ITEMS, ROYAL_SOLDIER, item, stock
from support import visible

pytestmark = pytest.mark.anyio

STORE = "/bfheroes/store"


async def listed(client: httpx2.AsyncClient, base: str, **chosen: str) -> str:
    """Return the text the store front shows for some choices of its form."""
    page = await client.get(f"{base}{STORE}", params=chosen)
    assert page.status_code == 200, page.text
    return visible(page.content)


@pytest.mark.usefixtures("prompt_actions")
async def test_the_store_front_lists_what_each_hero_can_have_and_holds(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with httpx2.AsyncClient() as client:
        closed = await listed(client, base)
    # The wave becomes a Royal Army emote, so that no emote fits a National Army hero.
    await stock(
        server_url, tmp_path, capsys, [*ITEMS[:-3], item(5001, "emote", faction="1"), *ITEMS[-2:]]
    )
    async with httpx2.AsyncClient() as client:
        signed_out = await listed(client, base)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        no_hero = await listed(client, base)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    mud = await create_hero(base, "ann@example.test", "Mudlark")
    # The coat rented for two more days, and three bandages left of the starting ones.
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, status, expires_at)"
        " SELECT account_id, id, '3-tlmd', 'ACTIVE', :ends FROM persona WHERE id = :hero",
        ends=datetime.now(UTC) + timedelta(days=2, hours=1),
        hero=brass,
    )
    await execute(
        database_url,
        "UPDATE entitlement SET use_count = 3 WHERE persona_id = :hero AND tag = '2005-lmct'",
        hero=brass,
    )

    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        default = await client.get(f"{base}{STORE}")
        mine = await listed(client, base, owned="1")
        mine_emotes = await listed(client, base, owned="1", kind="emote")
        mine_found = await listed(client, base, owned="1", q="coat")
        emotes = await listed(client, base, hero=str(mud), kind="emote")
        searched = await listed(client, base, q="tunic")
        numbered = await listed(client, base, q="3")
        polish = visible((await client.get(f"{base}/pl{STORE}")).content)
        tile = re.search(r'href="(/bfheroes/store/buy/\d+)\?hero=(\d+)"', default.text)
        assert tile is not None and tile.group(2) == str(brass)
        item_page = await client.get(f"{base}{tile.group(1)}", params={"hero": tile.group(2)})

    assert "once the game's catalogue is imported on this service" in closed
    # Every item sold alone, with its lowest price in each currency and who may have it; withheld
    # items, Hero Point abilities, missions and nameless items are no store front's.
    assert "Sign in to see what each hero owns and to buy." in signed_out
    assert "Invented Coat Royal Army from 1,300 Funds" in signed_out
    assert "Invented Rifle Royal Army · Soldier from 6,719 Funds · 150 Valor Points" in signed_out
    for left_out in ("Logo Shirt", "Partner Shirt", "Dash", "Mission", "Not owned"):
        assert left_out not in signed_out
    assert "Items are bought for a hero. Every item can be browsed meanwhile." in no_hero

    # The default hero's list leaves the other faction's items out and says what it holds.
    shown = visible(default.content)
    assert "Invented Tunic" not in shown and "Invented Longrifle" not in shown
    assert "Invented Coat Royal Army from 1,300 Funds Owns it · 2 days left · running low" in shown
    assert "Invented Bandage Any hero from 175 Funds 3 uses left · running low" in shown
    assert "Invented Monkey Royal Army from 899 Funds Not owned" in shown
    # Owned by is the hero's inventory, running low first; a category or a search counts only what
    # it lists.
    assert "Brassbuckle owns 3 items 2 running low · shown first" in mine
    assert (
        mine.index("Invented Coat")
        < mine.index("Invented Bandage")
        < mine.index("Owns it permanently")
    )
    assert "Invented Monkey" not in mine
    assert "Emotes owned by Brassbuckle · 0 Brassbuckle owns no item here." in mine_emotes
    assert "1 item owned by Brassbuckle matches 1 running low · shown first" in mine_found
    # A category without anything for the hero says why and offers the hero it fits.
    assert "Nothing in Emotes fits Mudlark." in emotes
    assert "on sale is for the Royal Army, and Mudlark is in the National Army." in emotes
    assert "Show them for Brassbuckle" in emotes
    assert "Nothing called “tunic” for Brassbuckle." in searched
    assert "Show them for Mudlark" in searched
    assert "Invented Coat" in numbered and "Invented Monkey" not in numbered
    assert "Brassbuckle:" in polish and "Ekwipunek: Brassbuckle" in polish

    # A tile leads to its item's page, whose way back keeps the hero.
    assert item_page.status_code == 200
    assert f'href="/bfheroes/store?hero={brass}" class="store-back"' in item_page.text
