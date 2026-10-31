from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import execute
from identity import IdentityProvider, sign_in
from living import freeze, import_catalogue
from support import visible
from test_admin_heroes import WARDEN
from test_rewards import INVENTED

pytestmark = pytest.mark.anyio

REWARDS = "/admin/rewards/bfheroes"
# The tenth day of Halloween's trick-or-treat, while its missions run.
HALLOWEEN = datetime(2026, 10, 28, 12, tzinfo=UTC)


@pytest.fixture
def halloween(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch, HALLOWEEN)


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_every_role_reads_the_years_calendar_missions_packs_and_milestones(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next="/admin/")
        first = await client.get(f"{base}/admin/rewards")
        page = visible((await client.get(f"{base}{first.headers['location']}")).content)
        polish = visible((await client.get(f"{base}/pl{REWARDS}")).content)
        unknown = await client.get(f"{base}/admin/rewards/nothing")

    assert first.headers["location"] == REWARDS
    assert "Rewards Rewards page of Battlefield Heroes" in page
    assert "Calendar 2026 Today 28 Oct · days start 00:00 UTC" in page
    assert "Calendar events 8 of 9 on" in page
    assert "E1 Valentine's Day" in page and "13\u201315 Feb Fixed days Ended" in page
    assert "E2 Easter" in page and "3\u20136 Apr Relative to Easter Ended" in page
    assert "19\u201331 Oct 13 daily gifts from 19 Oct Running · day 10 of 13" in page
    assert "19\u201331 Oct Goals before 1 Nov Running · until 31 Oct" in page
    assert "E8 Rising Sun mission" in page and "21\u201330 Nov Goals before 1 Dec Off" in page
    assert "1\u201324 Dec 24 daily gifts from 1 Dec Next · 1 Dec" in page
    # The game's texts were not imported, so the missions take their events' names.
    assert (
        "E5 Halloween machete mission Goal 700 · stat kw106 · Commando · Royal Army · before"
        " 1 Nov · gives Invented Golden Machete · permanent Running · until 31 Oct"
    ) in page
    assert "Missions 4 of 6 running" in page
    assert "Starter packs Each new hero may claim one for 30 days" in page
    assert "Royal Army 3025 · 7 days + the shared items Invented Rifle · 7 days" in page
    assert "Invented Stamina Boost · 7 days, Invented XP Boost · 7 days" in page
    assert "K1 · First chest key 1 key, once per account" in page
    assert "Milestones 5 of 5 on" in page
    assert "M3 Level 20 Per hero 1,500 Funds On" in page
    assert "Kalendarz 2026 Dziś 28 paź · dzień zaczyna się o 00:00 UTC" in polish
    assert "Trwa · dzień 10 z 13" in polish
    assert unknown.status_code == 404


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_an_events_page_shows_its_days_gifts_goals_texts_and_paused_days(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    await execute(
        database_url,
        "INSERT INTO reward_pause (game, day) VALUES ('bfheroes', '2026-10-21'),"
        " ('bfheroes', '2026-10-22'), ('bfheroes', '2026-02-14')",
    )
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next="/admin/")
        daily = visible((await client.get(f"{base}{REWARDS}/events/halloween")).content)
        christmas = visible((await client.get(f"{base}{REWARDS}/events/christmas")).content)
        mission = visible((await client.get(f"{base}{REWARDS}/events/halloween-machete")).content)
        unknown = await client.get(f"{base}{REWARDS}/events/harvest")

    assert "E4 Halloween trick-or-treat Running · day 10 of 13" in daily
    assert "Date rule 13 daily gifts from 19 Oct First day 19 Oct Last day 31 Oct Days 13" in daily
    assert "Day starts 00:00 UTC · every event" in daily
    assert "each can be claimed only on its day, unless claims were paused that day" in daily
    assert "One roll of" in daily and "worth at least 999 Funds" in daily
    assert "Claims were paused on 21 Oct and 22 Oct." in daily
    assert "REWARDS_HALLOWEEN_DAY_{day}_ROYAL_NAME Comes with the game's texts" in daily
    assert "Days with other gifts In place of that day's gift Royal Army National Army" in christmas
    assert "Day 17" in christmas and "Claims were not paused during this event" in christmas
    assert "Royal Army On Invented Golden Machete · permanent Goal 700" in mission
    assert "Counted stat kw106 Class Commando Counted from 19 Oct Before 1 Nov" in mission
    assert (
        "Each hero counts from 19 Oct; a goal reached can be claimed until the end of 31 Oct"
        in (mission)
    )
    assert unknown.status_code == 404
