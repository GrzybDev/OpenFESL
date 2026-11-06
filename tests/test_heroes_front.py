from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, post_sign_in
from heroes import create_hero
from living import ROYAL, freeze, import_catalogue, worn
from store import ROYAL_SOLDIER, stock, without_rotation
from support import Products, visible
from test_hot_deals_page import BANDAGES, COAT, PET
from test_profiles import name_players, play
from test_rewards import CHRISTMAS

from openfesl.core.store import Bundle

pytestmark = pytest.mark.anyio

FRONT = "/bfheroes"


async def test_the_front_page_tells_the_event_where_the_days_gifts_stand_and_the_weeks_best(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await name_players(database_url)
    # The hero's score of this week's two hours of rounds; another hero played only one.
    await play(database_url, hero, {"rs": 4810, "ct": 7200})
    brief = await create_hero(base, "bob@example.test", "Kettlebrook", **ROYAL_SOLDIER)
    await play(database_url, brief, {"rs": 9000, "ct": 3600})
    freeze(monkeypatch, CHRISTMAS)
    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{FRONT}")).content)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        shown = visible((await client.get(f"{base}{FRONT}")).content)
        polish = visible((await client.get(f"{base}/pl{FRONT}")).content)

    assert "Christmas gifts 1\u201324 Dec · day 5 of 24" in signed_out
    assert "Royal Army National Army" in signed_out
    assert "Sign in to claim today's gifts." in signed_out
    assert "Play Now starts Battlefield Heroes as the account's default hero" in signed_out
    assert "Nobody is playing right now." in signed_out
    # The Royal gift waits for the account's hero; the National one needs a hero it lacks.
    assert "Royal Army Today's gift waits · for Brassbuckle Claim on the rewards page" in shown
    assert "National Army Needs a National Army hero." in shown
    assert "Next gifts at 00:00 UTC" in shown
    assert "Top heroes All leaderboards" in shown
    assert "UTC; rankings are at most an hour old." in shown
    assert "Royal Army 100% National Army 0% Place 1 Brassbuckle Royal Army Soldier" in shown
    assert "Kettlebrook" not in shown.partition("Top heroes")[2]
    assert "Score 4,810" in shown
    assert "Signed in as Quill_Invented" in shown
    assert "Dzisiejszy prezent czeka · dla: Brassbuckle" in polish


async def test_a_week_without_an_event_or_a_hero_on_its_board_says_what_comes(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await name_players(database_url)
    # An hour and a half of ranked rounds this week, short of the board's two hours.
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await play(database_url, hero, {"rs": 4000, "ct": 5400})
    freeze(monkeypatch, datetime(2026, 3, 2, tzinfo=UTC))
    async with httpx2.AsyncClient() as client:
        shown = visible((await client.get(f"{base}{FRONT}")).content)

    assert "No event this week Next: Easter from 3 Apr." in shown
    assert "No hero is on this week's board yet." in shown
    assert "A hero shows once it has played 2 hours of rounds in the week." in shown


@pytest.mark.usefixtures("prompt_actions")
async def test_an_event_of_one_gift_tells_where_each_factions_gift_stands(
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The second of Valentine's three days, whose Royal gift fits the account's soldier.
    freeze(monkeypatch, datetime(2027, 2, 14, 12, tzinfo=UTC))
    invented = {
        1127: worn("Invented Heart Hat", ROYAL, "1"),
        2172: ("Invented Cupid Flare", "ability", {"faction": ROYAL, "consumable": "true"}),
    }
    await import_catalogue(server_url, tmp_path, capsys, invented)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{FRONT}")).content)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        shown = visible((await client.get(f"{base}{FRONT}")).content)

    assert "Sign in to claim its gifts." in signed_out and "today" not in signed_out
    assert "Royal Army Waits · for Brassbuckle Claim on the rewards page" in shown
    assert "National Army Needs a National Army hero." in shown


@pytest.mark.usefixtures("prompt_actions")
async def test_the_deal_of_the_day_shows_its_discount_items_and_who_may_buy_it(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    products: Products,
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    await products.add_bundle(
        "bfheroes",
        Bundle(
            "Invented Royal Deal",
            None,
            "_PF",
            2000,
            original_price=3675,
            faction="royal",
            members=(COAT, PET, BANDAGES),
            section="daily",
        ),
    )
    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{FRONT}")).content)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        heroless = visible((await client.get(f"{base}{FRONT}")).content)

    assert (
        "Deal of the Day Royal Army -45% 45% off Invented Coat · permanent Invented Monkey ·"
        " permanent Invented Bandage · 25 uses was 3,675 2,000 Funds See the deal"
    ) in signed_out
    assert "Create a hero first" not in signed_out
    assert (
        "Create a hero first: deals are bought for one hero of the deal's faction. Create a hero"
    ) in heroless
