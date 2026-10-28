from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, post_sign_in
from heroes import create_hero
from store import CHEER, ITEMS, ROYAL_SOLDIER, stock
from support import visible

pytestmark = pytest.mark.anyio

# The invented catalogue's products: the Royal coat for 30 days, 25 bandages and the cheer for
# good where it is stocked.
COAT_MONTH, BANDAGES, CHEER_FOR_GOOD = 2, 6, 25
PAGE = "/bfheroes/store/buy/{}"


async def shown(client: httpx2.AsyncClient, base: str, number: int, **chosen: int) -> str:
    """Return the text a store page shows, for a hero and a price if chosen."""
    page = await client.get(f"{base}{PAGE.format(number)}", params=chosen)
    assert page.status_code == 200, page.text
    return visible(page.content)


@pytest.mark.usefixtures("prompt_actions")
async def test_a_store_page_shows_each_hero_what_it_holds_or_why_it_cannot_have_the_item(
    base: str, server_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await stock(server_url, tmp_path, capsys, [*ITEMS, CHEER])
    async with httpx2.AsyncClient() as client:
        signed_out = await shown(client, base, COAT_MONTH)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        no_hero = await shown(client, base, COAT_MONTH)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    mud = await create_hero(base, "ann@example.test", "Mudlark")

    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        offered = await shown(client, base, COAT_MONTH)
        other = await shown(client, base, COAT_MONTH, hero=mud)
        # Every hero starts with bandages, to which bought uses add, and the game grants it the
        # cheer.
        bandages = await shown(client, base, BANDAGES)
        cheer = await shown(client, base, CHEER_FOR_GOOD)
        polish = visible((await client.get(f"{base}/pl{PAGE.format(COAT_MONTH)}")).content)

    assert "Sign in to buy" in signed_out
    assert "Requires Royal Army, any class, any level" in signed_out
    assert "the account has no hero yet" in no_hero and "4 slots are free" in no_hero
    assert "Brassbuckle Soldier · level 1 Not owned" in offered
    assert "Mudlark Commando · level 1 Royal Army only" in offered
    assert "30 days 1,300 Funds Permanent 2,600 Funds 100 short" in offered
    assert "Brassbuckle gets Invented Coat for 30 days, until" in offered
    assert "Price 1,300 Funds Funds now 2,500 After 1,200" in offered
    assert (
        "Invented Coat is for the Royal Army only, and Mudlark is in the National Army. "
        "Another hero of the account can get it."
    ) in other
    assert "Brassbuckle Soldier · level 1 10 uses left" in bandages
    assert "Brassbuckle gets 25 uses of Invented Bandage: 10 + 25 = 35." in bandages
    assert "Brassbuckle Soldier · level 1 Owns it permanently" in cheer
    assert "Brassbuckle owns Invented Cheer permanently. There is nothing more to buy" in cheer
    assert "Kup dla jednego bohatera" in polish and "Tylko Royal Army" in polish
