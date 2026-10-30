from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from heroes import create_hero
from living import freeze, import_catalogue, setting
from store import ROYAL_SOLDIER
from support import visible
from test_rewards import CHRISTMAS, INVENTED

pytestmark = pytest.mark.anyio

REWARDS = "/bfheroes/rewards"


@pytest.fixture
def christmas(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch, CHRISTMAS)


@pytest.mark.usefixtures("christmas", "prompt_actions")
async def test_the_rewards_page_shows_each_gift_with_the_heroes_it_may_go_to_and_how_far_goals_are(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(
        base, "ann@example.test", "Cutthroat", faction="royal", **{"class": "commando"}
    )
    # Made a month before Christmas, as the database dates heroes by its own clock.
    await execute(database_url, "UPDATE persona SET created_at = '2026-11-01 12:00+00'")
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        shown = visible((await client.get(f"{base}{REWARDS}")).content)
        polish = visible((await client.get(f"{base}/pl{REWARDS}")).content)

    # The game's texts were not imported, so the event's own name stands in for the gift's.
    assert "Christmas gifts, day 5 of 24." in shown
    assert "Day 5 of 24 · until 00:00 UTC" in shown
    assert "One item at random, worth at least 999 Funds" in shown
    assert "Ready · for Brassbuckle or Cutthroat" in shown
    # The National gift needs a hero the account lacks, and it has a slot free for one.
    assert "Needs a National Army hero. Slot 3 is free. Create a hero" in shown
    assert "First hero · 600 Funds" in shown and "Account Goal reached" in shown
    assert "Brassbuckle Level 1 of 10" in shown
    # The heroes were made more than a month before Christmas, so their packs ran out.
    assert "Starter pack · Cutthroat Not claimed" in shown
    assert "Coming up: Valentine's Day from 13 Feb" in shown
    assert "Świąteczne prezenty, dzień 5 z 24." in polish


@pytest.mark.usefixtures("christmas")
async def test_visitors_see_the_running_events_and_an_account_without_a_hero_the_creator(
    base: str,
) -> None:
    async with httpx2.AsyncClient() as client:
        signed_out = await client.get(f"{base}{REWARDS}")
        await post_sign_in(client, base, "bob@example.test", PASSWORD)
        heroless = visible((await client.get(f"{base}{REWARDS}")).content)

    shown = visible(signed_out.content)
    assert "Both factions Christmas gifts 1" in shown and "Dec · One free gift a day" in shown
    assert "Sign in to claim" in shown
    assert 'name="key"' not in signed_out.text
    assert "No hero yet" in heroless and "brings 600 Funds as a milestone" in heroless


@pytest.mark.usefixtures("prompt_actions")
async def test_a_starter_packs_last_day_is_a_day_of_the_rewards_zone_also_with_summer_time(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    await setting(database_url, "bfheroes.rewards.zone", "Europe/Warsaw")
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    # Its pack's last day is 28 March, the day summer time starts, which has 23 hours.
    created = "UPDATE persona SET created_at = '2027-02-26 12:00+00' WHERE id = :hero"
    await execute(database_url, created, hero=hero)
    shown = []
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        for day in (27, 28, 29):
            freeze(monkeypatch, datetime(2027, 3, day, 12, tzinfo=UTC))
            shown.append(visible((await client.get(f"{base}{REWARDS}")).content))

    before, last, after = shown
    assert "Claim by 28 Mar · 1 day left" in before
    assert "Claim by 28 Mar · last day" in last
    assert "Expired on 28 Mar" in after
