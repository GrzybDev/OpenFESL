from pathlib import Path
from xml.etree import ElementTree

import httpx2
import pytest
from accounts import PASSWORD, post_sign_in, select_value
from heroes import MAGMA, wallets
from store import ROYAL_SOLDIER, buy, grant, items, shopper, stock
from support import fetch, visible

pytestmark = pytest.mark.anyio

# The invented catalogue's products: the first two levels of the dash, for a Hero Point each,
# and 25 bandages for Funds.
DASH_1, DASH_2, BANDAGES = 8, 9, 6


async def refund(server_url: str, cookie: dict[str, str], hero: int) -> ElementTree.Element:
    """Refund a hero's abilities as the game's reset button does."""
    path = f"/nucleus/refundAbilities/{hero}"
    return ElementTree.fromstring((await fetch(server_url, path, MAGMA, cookie, b""))[2])


@pytest.mark.usefixtures("prompt_actions")
async def test_a_refund_gives_back_the_hero_points_paid_and_takes_back_what_they_bought(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    other_cookie, other = await shopper(server_url, "bob@example.test", "Mudlark")
    await grant(database_url, hero, "_DH", 3)
    nothing = await refund(server_url, cookie, hero)
    bought = [
        await buy(server_url, cookie, hero, product) for product in (DASH_1, DASH_2, BANDAGES)
    ]

    done = await refund(server_url, cookie, hero)
    after = await items(server_url, cookie, hero)
    again = await refund(server_url, cookie, hero)
    # A level bought again after the refund, and a refund for another account's hero.
    rebought = await buy(server_url, cookie, hero, DASH_1)
    refused = await refund(server_url, other_cookie, hero)

    assert (nothing.tag, nothing.get("code")) == ("success", "NO_CHANGE")
    assert bought == [("success", None)] * 3
    assert (done.tag, done.get("code")) == ("success", "REFUNDED")
    assert (done.findtext("walletAccount/currency"), done.findtext("walletAccount/balance")) == (
        "_DH",
        "3",
    )
    assert [item.text for item in done.iterfind("refunded/item")] == ["2096", "2097"]
    # The bandages the hero started with and those bought with Funds stay.
    assert after == ["2005-lmct 35"]
    assert again.get("code") == "NO_CHANGE"
    assert rebought == ("success", None)
    assert refused.tag == "error"
    assert await items(server_url, other_cookie, other) == ["2005-lmct 10"]
    # What the hero holds and has spent on what it owns is what it was ever given.
    balance = (await wallets(server_url, cookie, hero))["_DH"]
    spent = await select_value(
        database_url,
        "SELECT sum(price) FROM purchase WHERE persona_id = :hero AND currency = '_DH'"
        " AND refunded_at IS NULL",
        hero=hero,
    )
    given = await select_value(
        database_url,
        "SELECT sum(delta) FROM wallet_entry JOIN wallet ON wallet.id = wallet_id"
        " WHERE persona_id = :hero AND reason IN ('admin', 'level', 'server')",
        hero=hero,
    )
    assert (balance, spent, given) == (2, 1, 3)
    ledger = await select_value(
        database_url,
        "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY wallet_entry.id) FROM wallet_entry"
        " JOIN wallet ON wallet.id = wallet_id WHERE persona_id = :hero",
        hero=hero,
    )
    assert ledger == "admin:3 purchase:-1 purchase:-1 refund:2 purchase:-1"
    # The heroes page says the hero earned what it was given, not what refunds gave back.
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        shown = visible((await client.get(f"{base}/bfheroes/heroes")).content)
    assert "2 unspent of 3" in shown
