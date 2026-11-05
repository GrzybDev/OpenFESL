import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import pytest
from accounts import select_value
from heroes import MAGMA, wallets
from store import CHEER, ITEMS, ROYAL_SOLDIER, buy, grant, items, kept_by_server, shopper, stock
from support import fetch

pytestmark = pytest.mark.anyio

# The invented catalogue's products, numbered as the store lists them: the Royal coat for 30
# days, the National tunic, the Royal pet, 25 bandages, the three levels of the dash, the Royal
# soldier's rifle for a day, the National commando's rifle and the wave, then the cheer for good
# and for 30 days where it is stocked.
COAT_MONTH, TUNIC, PET, BANDAGES = 2, 3, 5, 6
DASH_1, DASH_2, DASH_3 = 8, 9, 10
RIFLE_DAY, LONGRIFLE, WAVE = 14, 16, 19
CHEER_FOR_GOOD, CHEER_MONTH = 21, 22


async def ends(database_url: str, hero: int, item: int) -> datetime:
    """Return when a hero's rental of an item ends."""
    end = await select_value(
        database_url,
        "SELECT expires_at FROM entitlement WHERE persona_id = :hero AND tag LIKE :item",
        hero=hero,
        item=f"{item}-%",
    )
    assert isinstance(end, datetime)
    return end


@pytest.mark.usefixtures("prompt_actions")
async def test_a_hero_buys_with_its_accounts_funds_and_valor_and_its_own_hero_points(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await grant(database_url, hero, "_DV", 600)
    await grant(database_url, hero, "_DH", 3)

    bought = [await buy(server_url, cookie, hero, PET)]
    bought.append(await buy(server_url, cookie, hero, RIFLE_DAY))
    first_end = await ends(database_url, hero, 3015)
    # A rental bought again lasts a day longer from where it ended, and uses add up.
    bought += [await buy(server_url, cookie, hero, product) for product in (RIFLE_DAY, BANDAGES)]
    second_end = await ends(database_url, hero, 3015)
    # An ability's levels in order: the third before the second is refused.
    levels = [
        await buy(server_url, cookie, hero, product) for product in (DASH_3, DASH_1, DASH_2, DASH_3)
    ]

    assert bought == [("success", None)] * 4
    assert levels == [
        ("error", "LEVEL_MISSING"),
        ("success", None),
        ("success", None),
        ("success", None),
    ]
    assert second_end - first_end == timedelta(days=1)
    # Funds and Valor Points from the account, Hero Points from the hero.
    assert await wallets(server_url, cookie, hero) == {"_DH": 0, "_DV": 300, "_PF": 1426}
    assert await items(server_url, cookie, hero) == [
        "2005-lmct 35",
        "521-unlm 0",
        "3015-tlmd 0",
        "2096-unlm 0",
        "2097-unlm 0",
        "2098-unlm 0",
    ]
    recorded = await select_value(
        database_url,
        "SELECT string_agg(product_id || ' ' || currency || price || ' ' || outcome::text, ', '"
        " ORDER BY id) FROM purchase WHERE persona_id = :hero AND kind = 'product'",
        hero=hero,
    )
    assert recorded == (
        'OFB-DBFH:5 _PF899 {"items": [521]}, OFB-DBFH:14 _DV150 {"items": [3015]}, '
        'OFB-DBFH:14 _DV150 {"items": [3015]}, OFB-DBFH:6 _PF175 {"items": [2005]}, '
        'OFB-DBFH:8 _DH1 {"items": [2096]}, OFB-DBFH:9 _DH1 {"items": [2097]}, '
        'OFB-DBFH:10 _DH1 {"items": [2098]}'
    )


@pytest.mark.usefixtures("prompt_actions")
async def test_a_purchase_the_hero_may_not_make_takes_nothing_and_gives_nothing(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, [*ITEMS, CHEER])
    cookie, hero = await shopper(
        server_url, "ann@example.test", "Mudlark", faction="national", **{"class": "soldier"}
    )
    _, other = await shopper(server_url, "bob@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    assert await buy(server_url, cookie, hero, WAVE) == ("success", None)

    refused = [
        # The wave again, which the hero owns for good, and the cheer the game grants it.
        await buy(server_url, cookie, hero, WAVE),
        await buy(server_url, cookie, hero, CHEER_FOR_GOOD),
        await buy(server_url, cookie, hero, CHEER_MONTH),
        # Another faction's pet and another class's rifle.
        await buy(server_url, cookie, hero, PET),
        await buy(server_url, cookie, hero, LONGRIFLE),
        # A tunic for more Funds than the account has left.
        await buy(server_url, cookie, hero, TUNIC),
        # Products the store does not sell, and a hero of another account.
        await buy(server_url, cookie, hero, 999),
        await buy(server_url, cookie, hero, "1x"),
        await buy(server_url, cookie, other, COAT_MONTH),
    ]

    assert refused == [
        ("error", "ALREADY_OWNED"),
        ("error", "ALREADY_OWNED"),
        ("error", "ALREADY_OWNED"),
        ("error", "NOT_FOR_HERO"),
        ("error", "NOT_FOR_HERO"),
        ("error", "INSUFFICIENT_FUNDS"),
        ("error", "UNKNOWN_PRODUCT"),
        ("error", "UNKNOWN_PRODUCT"),
        ("error", "USER_DOES_NOT_MATCH"),
    ]
    assert (await wallets(server_url, cookie, hero))["_PF"] == 2500 - 1679
    assert await items(server_url, cookie, hero) == ["2005-lmct 10", "5001-unlm 0"]
    assert await select_value(database_url, "SELECT count(*) FROM purchase") == 1


@pytest.mark.usefixtures("prompt_actions", "open_hosting")
async def test_a_server_cannot_buy_for_the_player_whose_data_it_keeps(
    server_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await stock(server_url, tmp_path, capsys)
    async with kept_by_server(server_url) as (cookie, hero, _):
        _, _, kept = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
        bought = await buy(server_url, cookie, hero, PET)

    # The server reads its player's items, yet buys nothing with the player's Funds.
    assert ElementTree.fromstring(kept).tag == "entitlements"
    assert bought == ("error", "USER_DOES_NOT_MATCH")


@pytest.mark.usefixtures("prompt_actions")
async def test_purchases_at_once_never_spend_more_than_the_wallet_holds(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)

    # The account's 2,500 Funds pay for one 30-day coat of 1,300.
    answers = await asyncio.gather(*(buy(server_url, cookie, hero, COAT_MONTH) for _ in range(3)))
    ledger = await select_value(
        database_url,
        "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY wallet_entry.id) FROM wallet_entry"
        " JOIN wallet ON wallet.id = wallet_id WHERE currency = '_PF'",
    )
    rental = await ends(database_url, hero, 3)
    started = await select_value(database_url, "SELECT created_at FROM purchase")
    assert isinstance(started, datetime)

    assert sorted(answers) == [
        ("error", "INSUFFICIENT_FUNDS"),
        ("error", "INSUFFICIENT_FUNDS"),
        ("success", None),
    ]
    assert ledger == "allowance:2500 purchase:-1300"
    # One rental of 30 days, never lengthened by the refused ones.
    assert rental - started < timedelta(days=30, minutes=1)
