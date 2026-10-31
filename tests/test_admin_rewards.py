from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import execute, select_value
from identity import IdentityProvider, form_token, sign_in
from living import freeze, import_catalogue
from support import visible
from test_admin_deals import setting
from test_admin_heroes import KEEPER, WARDEN
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


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_admins_pause_and_resume_every_claim_with_a_note_that_home_and_the_log_show(
    admin_provider: IdentityProvider, base: str, database_url: str
) -> None:
    pause = f"{base}{REWARDS}/pause"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next=REWARDS)).content)
        assert "Pause of claims Every switched-on reward can be claimed." in shown
        assert "Not paused" in shown and "Pause claims" not in shown
        assert (await client.get(pause)).status_code == 403
        token = form_token((await client.get(f"{base}{REWARDS}")).text)
        refused = await client.post(pause, data={"csrf": token, "paused": "1", "note": "busy"})
        assert refused.status_code == 403
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=REWARDS)).text)
        asked = visible((await client.get(pause)).content)
        assert "Pause every claim of the rewards?" in asked
        assert "Missions and milestones keep counting." in asked
        wanted = {"csrf": token, "paused": "1"}
        unnoted = await client.post(pause, data=wanted)
        assert unnoted.status_code == 400
        assert "Still needed: a note." in visible(unnoted.content)
        noted = wanted | {"note": "site load during the event"}
        paused = visible((await client.post(pause, data=noted, follow_redirects=True)).content)
        twice = visible((await client.post(pause, data=noted, follow_redirects=True)).content)
        home = visible((await client.get(f"{base}/admin/")).content)
        resumed = await client.post(
            pause, data={"csrf": token, "paused": "0", "note": "load back to normal"}
        )
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert "Paused the claims of the rewards; the players' page says so" in paused
    assert "by invented-keeper: site load during the event. No reward can be claimed" in paused
    assert "Running · day 10 of 13" not in paused and "Paused · day 10 of 13" in paused
    assert "Rewards 1 needing attention" in paused
    assert "The claims of the rewards were paused already." in twice
    assert "Rewards 1 Rewards paused Rewards page of Battlefield Heroes: paused since" in home
    # The day is recorded at once, so that its daily gifts wait however soon claims resume.
    day = "SELECT count(*) FROM reward_pause WHERE day = '2026-10-28'"
    assert await select_value(database_url, day) == 1
    assert resumed.status_code == 303
    key = "SELECT count(*) FROM setting WHERE key = 'bfheroes.rewards.paused'"
    assert await select_value(database_url, key) == 0
    assert "Rewards resumed Rewards page of Battlefield Heroes Details load back to normal" in log
    assert "Rewards paused Rewards page of Battlefield Heroes Details site load" in log


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_admins_switch_events_parts_packs_and_the_key_and_change_milestones(
    admin_provider: IdentityProvider, base: str, database_url: str
) -> None:
    switch, milestones = f"{base}{REWARDS}/switch", f"{base}{REWARDS}/milestones"
    funds = {
        "funds-first-hero": "600",
        "funds-level-10": "500",
        "funds-level-20": "1200",
        "funds-level-30": "3000",
        "funds-matches": "1000",
    }
    on = {f"on-{key}": "1" for key in ("first-hero", "level-10", "level-20", "level-30")}
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next=REWARDS)).content)
        token = form_token((await client.get(f"{base}{REWARDS}")).text)
        flipped = await client.post(switch, data={"csrf": token, "path": "events.bento"})
        saved = await client.post(milestones, data={"csrf": token, **funds})
    assert "Rewards are paused and changed with the admin role." in shown
    assert "Save milestones" not in shown
    assert flipped.status_code == saved.status_code == 403

    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=REWARDS)).text)

        async def flip(path: str, enabled: str = "") -> str:
            form = {"csrf": token, "path": path, "enabled": enabled, "back": REWARDS}
            return visible((await client.post(switch, data=form, follow_redirects=True)).content)

        off = await flip("events.bento")
        half = await flip("events.halloween.parts.national")
        await flip("starter")
        await flip("first_chest")
        unknown = await client.post(switch, data={"csrf": token, "path": "events.harvest"})
        refused = await client.post(
            milestones, data={"csrf": token, **funds, "funds-level-10": "0", **on}
        )
        changed = visible(
            (
                await client.post(
                    milestones, data={"csrf": token, **funds, **on}, follow_redirects=True
                )
            ).content
        )
        back = await flip("events.bento", "1")
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert "Switched off Bento week; it applies from the next claim" in off
    assert "Calendar events 7 of 9 on" in off
    assert "Switched off Halloween trick-or-treat · National Army" in half
    assert (
        await setting(database_url, "bfheroes.rewards.events.halloween.parts.national.enabled")
        == "off"
    )
    assert await setting(database_url, "bfheroes.rewards.starter.enabled") == "off"
    assert await setting(database_url, "bfheroes.rewards.first_chest.enabled") == "off"
    assert unknown.status_code == 404
    assert refused.status_code == 400
    assert "What Level 10 gives is a whole number of Funds from 1 to 2,147,483,647." in visible(
        refused.content
    )
    assert "Saved the milestones; changes apply from the next claim." in changed
    assert "Milestones 4 of 5 on" in changed
    assert await setting(database_url, "bfheroes.rewards.milestones.level-20.gift.funds") == "1200"
    assert await setting(database_url, "bfheroes.rewards.milestones.matches.enabled") == "off"
    # Back at the game data's value, a switch leaves the settings.
    assert "Switched on Bento week" in back
    assert await setting(database_url, "bfheroes.rewards.events.bento.enabled") is None
    assert "Reward switched on Bento week" in log and "Reward switched off Bento week" in log
    assert "Reward switched off Halloween trick-or-treat · National Army" in log
    assert "Milestone changed Level 20 Details 1,500 → 1,200 Funds" in log
    assert "Milestone changed Ranked matches: 100 Details On → Off" in log
