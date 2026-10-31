from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from identity import IdentityProvider, form_token, sign_in
from living import freeze, import_catalogue
from living import setting as change
from playwright.async_api import Browser
from store import ROYAL_SOLDIER
from support import form_fields, visible
from test_admin_deals import setting
from test_admin_heroes import KEEPER, WARDEN
from test_rewards import INVENTED, kills

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
    assert "Rewards are paused and changed with the admin role." in daily
    assert "Date rule Fixed days From one date to another Relative to Easter" in daily
    assert "Daily count A first day and a number of days" in daily
    assert "Day starts 00:00 UTC · every event" in daily
    assert (
        "13 daily gifts, 19 Oct to 31 Oct; each can be claimed only on its day, unless claims"
        " were paused that day."
    ) in daily
    assert "Save event" not in daily
    assert "One roll of" in daily and "worth at least 999 Funds" in daily
    assert "Claims were paused on 21 Oct and 22 Oct." in daily
    assert "REWARDS_HALLOWEEN_DAY_{day}_ROYAL_NAME Comes with the game's texts" in daily
    assert "Days with other gifts In place of that day's gift Royal Army National Army" in christmas
    assert "Day 17" in christmas and "Claims were not paused during this event" in christmas
    assert "Royal Army Royal Army: On Invented Golden Machete · permanent" in mission
    assert "Goal Counted stat kw106 Class Commando Counted from 19 Oct Before 1 Nov" in mission
    assert (
        "Each hero counts from 19 Oct; a goal reached can be claimed until the end of 31 Oct"
        in (mission)
    )
    assert unknown.status_code == 404


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_admins_change_an_events_days_with_own_text_where_the_games_texts_end(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    christmas, events = f"{base}{REWARDS}/events/christmas", f"{base}{REWARDS}/events"
    longer = {"rule": "count", "first": "2026-12-01", "count": "25"}
    window, text = (
        "bfheroes.rewards.events.christmas.window",
        "bfheroes.rewards.events.christmas.own_text",
    )
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=f"{REWARDS}/events/christmas")).text)
        refused = await client.post(christmas, data={"csrf": token, **longer})
    assert refused.status_code == 403

    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=f"{REWARDS}/events/christmas")).text)
        # As the fields are typed, the page says what they set and what the texts need.
        typed = visible((await client.get(christmas, params=longer)).content)
        untexted = await client.post(christmas, data={"csrf": token, **longer})
        kept = await setting(database_url, window)
        too_long = await client.post(
            f"{events}/halloween",
            data={"csrf": token, "rule": "count", "first": "2026-10-19", "count": "400"},
        )
        backwards = await client.post(
            f"{events}/easter", data={"csrf": token, "rule": "easter", "before": "2", "after": "-1"}
        )
        # A run further from Easter could fall outside the years around today that are looked in.
        far = await client.post(
            f"{events}/easter",
            data={"csrf": token, "rule": "easter", "before": "-500", "after": "-400"},
        )
        leap = await client.post(
            f"{events}/valentine",
            data={"csrf": token, "rule": "fixed", "first": "2028-02-29", "last": "2028-03-01"},
        )
        texted = {"csrf": token, **longer, "text-en": "A gift a day until Christmas Day."}
        saved = visible(
            (
                await client.post(christmas, data=texted | {"text-pl": ""}, follow_redirects=True)
            ).content
        )
        saved_window, saved_text = (
            await setting(database_url, window),
            await setting(database_url, text),
        )
        overview = visible((await client.get(f"{base}{REWARDS}")).content)
        freeze(monkeypatch, datetime(2026, 12, 25, 12, tzinfo=UTC))
        async with httpx2.AsyncClient() as player:
            await post_sign_in(player, base, "ann@example.test", PASSWORD)
            page = visible((await player.get(f"{base}/bfheroes/rewards")).content)
            polish = visible((await player.get(f"{base}/pl/bfheroes/rewards")).content)
            claim = {"csrf": form_token((await player.get(f"{base}/bfheroes/rewards")).text)}
            claim |= {"key": "christmas:2026:25", "faction": "royal", "hero": str(hero)}
            claimed = await player.post(f"{base}/bfheroes/rewards/claim", data=claim)
        # Back at the game data's days, the own text may go, and with it the settings.
        back = {
            "csrf": token,
            "rule": "count",
            "first": "2026-12-01",
            "count": "24",
            "text-en": "",
            "text-pl": "",
        }
        await client.post(christmas, data=back)
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert "25 daily gifts, 1 Dec to 25 Dec" in typed
    assert (
        "The game's texts of this event cover days 1 to 24; a day 25 needs own text. English is"
        " required."
    ) in typed
    assert untexted.status_code == 400 and kept is None
    assert "Saving waits for the English own text." in visible(untexted.content)
    assert (
        too_long.status_code == backwards.status_code == far.status_code == leap.status_code == 400
    )
    assert (
        "Halloween trick-or-treat would run for 400 days, so a year's run would reach the next"
        " one; the runs of one event cannot overlap."
    ) in visible(too_long.content)
    assert "The first day comes after the last." in visible(backwards.content)
    assert "Days from Easter Sunday are whole numbers from -365 to 365" in visible(far.content)
    assert "29 Feb is not a day of every year" in visible(leap.content)
    assert "Saved Christmas gifts, 1\u201325 Dec; changes apply from the next claim." in saved
    assert saved_window == '{"first": "12-01", "count": 25}'
    assert saved_text == '{"en": "A gift a day until Christmas Day."}'
    assert "1\u201325 Dec 25 daily gifts from 1 Dec" in overview
    # The players' page and claims follow at once, the English text standing in for Polish.
    assert "Christmas gifts A gift a day until Christmas Day." in page
    assert "A gift a day until Christmas Day." in polish
    assert claimed.status_code == 303
    assert (
        await select_value(
            database_url, "SELECT count(*) FROM reward_claim WHERE key = 'christmas:2026:25'"
        )
        == 1
    )
    assert await setting(database_url, window) is None and await setting(database_url, text) is None
    assert (
        "Event changed Christmas gifts Details 1\u201324 Dec → 1\u201325 Dec; own text: English"
    ) in log
    assert (
        "Event changed Christmas gifts Details 1\u201325 Dec → 1\u201324 Dec; own text removed"
    ) in log


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_admins_change_what_each_part_gives_and_the_days_of_other_gifts(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    edge: Browser,
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    valentine, birthday, christmas = (
        f"{base}{REWARDS}/events/{key}" for key in ("valentine", "birthday", "christmas")
    )
    royal = "bfheroes.rewards.events.valentine.parts.royal.gift"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"{REWARDS}/events/valentine")
        # Without scripts, Find and Add answer the form as written and save nothing.
        fields = form_fields((await client.get(valentine)).text)
        found = visible(
            (await client.post(valentine, data=fields | {"q": ["Invented"], "find": ["1"]})).content
        )
        mask = {"to-1081": ["royal"], "own-1081": ["7d"]}
        unfit = await client.post(
            valentine, data=fields | {"add": ["1084"], "to-1084": ["royal"], "own-1084": ["perm"]}
        )
        added = await client.post(valentine, data=fields | {"add": ["1081"], **mask})
        unsaved = await setting(database_url, royal)
        await client.post(valentine, data=form_fields(added.text))
        saved = await setting(database_url, royal)
        fields = form_fields((await client.get(birthday)).text)
        emptied = fields | {
            "drop-national": [str(index) for index in range(len(fields["item-national"]))],
            "funds-national": ["0"],
            "keys-account": ["many"],
        }
        refused = await client.post(birthday, data=emptied)
        fields = form_fields((await client.get(christmas)).text)
        mask = {"to-1081": ["royal"], "own-1081": ["perm"], "add-day-1081": ["5"]}
        fifth = await client.post(christmas, data=fields | {"add": ["1081"], **mask})
        await client.post(christmas, data=form_fields(fifth.text) | {"drop-day": ["17"]})
        days = await setting(database_url, "bfheroes.rewards.events.christmas.parts.royal.days")
        log = visible((await client.get(f"{base}/admin/audit")).content)
        context = await edge.new_context(locale="en-GB")
        for name, value in client.cookies.items():
            await context.add_cookies([{"name": name, "value": value, "url": base}])
    # With scripts, Add answers the form while saving waits for the own text the first add needs.
    scripted = await context.new_page()
    await scripted.goto(birthday)
    saving = []
    for number in ("1081", "1084"):
        await scripted.locator("#event-q").fill(number)
        add = scripted.locator(f"button[name=add][value='{number}']")
        await add.wait_for()
        saving.append(
            await scripted.locator(".reward-save button[type=submit]").get_attribute(
                "aria-disabled"
            )
        )
        async with scripted.expect_navigation():
            await add.click()
    answered = await scripted.get_by_role("status").inner_text()
    await context.close()
    freeze(monkeypatch, datetime(2027, 2, 14, 12, tzinfo=UTC))
    async with httpx2.AsyncClient() as player:
        await post_sign_in(player, base, "ann@example.test", PASSWORD)
        page = visible((await player.get(f"{base}/bfheroes/rewards")).content)

    assert "Invented Mask item 1081" in found and "Invented Visor item 1084" in found
    assert unfit.status_code == 400
    assert (
        "Invented Visor is for the National Army, so it cannot be in the Royal Army half of"
        " Valentine's Day."
    ) in visible(unfit.content)
    assert added.status_code == 200 and unsaved is None
    assert "Added Invented Mask to the Royal Army half; Save event keeps it." in visible(
        added.content
    )
    assert saved == '{"items": ["1127 7d", "2172 x10", "1081 7d"]}'
    assert refused.status_code == 400
    shown = visible(refused.content)
    assert (
        "The National Army half of The game's birthday has no contents; switch the faction off"
        " or add an item."
    ) in shown
    assert "Funds and keys are whole numbers from 0 to 2,147,483,647." in shown
    assert "The texts of this event name what it gives; other contents need own text." in shown
    # A new day keeps the part's box for heroes whom its item does not fit; day 17 is gone.
    assert isinstance(days, str) and '"17"' not in days
    assert '"5": {"items": ["1081 perm"], "box": "royal-santas-visit"}' in days
    assert "Event changed Valentine's Day Details Royal Army: other contents" in log
    assert (
        "Event changed Christmas gifts Details Royal Army: other contents; National Army: other"
        " contents"
    ) in log
    # The players' page gives the new item at once.
    assert "Invented Mask · 7 days" in page
    assert saving == ["false", "true"]
    assert "Added Invented Visor to the National Army half" in answered


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_admins_change_a_missions_goal_with_own_text_as_the_games_states_the_goal(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    commando = await create_hero(
        base, "ann@example.test", "Cutthroat", faction="royal", **{"class": "commando"}
    )
    async with httpx2.AsyncClient() as player:
        # The first look takes the heroes' counts when the mission began.
        await post_sign_in(player, base, "ann@example.test", PASSWORD)
        await player.get(f"{base}/bfheroes/rewards")
    machete = f"{base}{REWARDS}/events/halloween-machete"
    goal = "bfheroes.rewards.events.halloween-machete.parts.royal.goal.count"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        fields = form_fields(
            (await sign_in(client, base, next=f"{REWARDS}/events/halloween-machete")).text
        )
        lower = fields | {"goal-royal": ["500"]}
        untexted = await client.post(machete, data=lower)
        zero = await client.post(machete, data=fields | {"goal-royal": ["0"]})
        texted = lower | {"text-en": ["500 machete kills on one Commando bring a golden machete."]}
        await client.post(machete, data=texted)
        saved = await setting(database_url, goal)
        overview = visible((await client.get(f"{base}{REWARDS}")).content)
        log = visible((await client.get(f"{base}/admin/audit")).content)
    await kills(database_url, commando, "kw106", 120)
    async with httpx2.AsyncClient() as player:
        await post_sign_in(player, base, "ann@example.test", PASSWORD)
        page = visible((await player.get(f"{base}/bfheroes/rewards")).content)

    assert untexted.status_code == zero.status_code == 400
    assert (
        "The game's texts of this event state a goal of 700; a goal of 500 needs own text."
        " English is required."
    ) in visible(untexted.content)
    assert "A goal is a whole number from 1 to 2,147,483,647." in visible(zero.content)
    assert saved == "500"
    assert "Goal 500 · stat kw106 · Commando · Royal Army" in overview
    assert (
        "Event changed Halloween machete mission Details Royal Army: goal 700 → 500; own text:"
        " English"
    ) in log
    # The players' page counts the hero's kills against the new goal, with the own text.
    assert "500 machete kills on one Commando bring a golden machete." in page
    assert "Cutthroat 120 of 500 kills" in page


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


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_admins_set_the_zone_at_whose_midnight_the_rewards_days_start(
    admin_provider: IdentityProvider, base: str, database_url: str
) -> None:
    zone = f"{base}/admin/settings/games/bfheroes/zone"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next="/admin/settings")).content)
        token = form_token((await client.get(f"{base}/admin/settings")).text)
        refused = await client.post(zone, data={"csrf": token, "zone": "Europe/Warsaw"})
    assert "Day start of the rewards UTC" in shown
    assert "Changing the zone of the rewards' days needs the admin role." in shown
    assert refused.status_code == 403

    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next="/admin/settings")).text)
        unknown = await client.post(zone, data={"csrf": token, "zone": "Mars/Olympus"})
        sent = {"csrf": token, "zone": "Europe/Warsaw"}
        saved = visible((await client.post(zone, data=sent, follow_redirects=True)).content)
        kept = await setting(database_url, "bfheroes.rewards.zone")
        page = visible((await client.get(f"{base}{REWARDS}")).content)
        await client.post(zone, data={"csrf": token, "zone": "UTC"})
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert unknown.status_code == 400
    assert "the time zone database knows no zone by this name" in visible(unknown.content)
    assert "The rewards' days start at midnight in Europe/Warsaw from now on." in saved
    assert kept == "Europe/Warsaw"
    assert "Today 28 Oct · days start 00:00 Europe/Warsaw" in page
    # Back at the game data's zone, the setting goes.
    assert await setting(database_url, "bfheroes.rewards.zone") is None
    assert "Day start changed Rewards page of Battlefield Heroes Details UTC → Europe/Warsaw" in log


@pytest.mark.usefixtures("halloween")
async def test_the_preview_shows_the_players_page_on_a_date_with_the_switches_applied(
    admin_provider: IdentityProvider, base: str, database_url: str
) -> None:
    preview = f"{base}{REWARDS}/preview"
    await change(database_url, "bfheroes.rewards.events.halloween-map.enabled", "off")
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        today = visible((await sign_in(client, base, next=f"{REWARDS}/preview")).content)
        christmas = visible((await client.get(preview, params={"date": "2026-12-09"})).content)
        quiet = visible((await client.get(preview, params={"date": "2026-03-02"})).content)
        wrong = visible((await client.get(preview, params={"date": "yesterday"})).content)

    assert "The players' rewards page on 28 Oct 2026 2 calendar rewards + the others" in today
    assert "E4 Claimable that day · day 10 of 13 Halloween trick-or-treat" in today
    assert "E5 Locked until the goal Halloween machete mission" in today
    # A switched-off event leaves the page.
    assert "Halloween map mission" not in today
    assert "Any time When it applies Starter pack For each hero made within the last 30 days." in (
        today
    )
    assert "Milestones 5 milestones, each given once it is met." in today
    assert "Today 21 Nov 1 Dec 13 Feb" in today
    assert "E9 Claimable that day · day 9 of 24 Christmas gifts" in christmas
    assert "No calendar reward on this date" in quiet
    assert "The players' rewards page on 28 Oct 2026" in wrong


@pytest.mark.usefixtures("halloween", "prompt_actions")
async def test_this_years_claims_list_who_claimed_what_for_which_hero_by_kind(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        token = form_token((await client.get(f"{base}/bfheroes/rewards")).text)
        for key, faction, chosen in (
            ("milestone:first-hero", "", ""),
            (f"starter:{hero}", "royal", str(hero)),
        ):
            form = {"csrf": token, "key": key, "faction": faction, "hero": chosen}
            await client.post(f"{base}/bfheroes/rewards/claim", data=form)
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        every = visible((await sign_in(client, base, next=f"{REWARDS}/claims")).content)
        packs = visible((await client.get(f"{base}{REWARDS}/claims?reward=starter")).content)
        bento = visible((await client.get(f"{base}{REWARDS}/claims?reward=bento")).content)

    assert "Claims · newest first 2 this year" in every
    assert (
        "28 Oct 2026 12:00 UTC Account ann@example.test Hero Brassbuckle Starter pack Given"
        " Invented Tonic · 25 uses, Invented Rifle · 7 days"
    ) in every
    assert "Hero \u2013 No hero M1 First hero Given 600 Funds for the account" in every
    assert "Every reward 2 Starter packs 1 Milestones 1" in every
    assert "Claims: 1" in packs and "First hero" not in packs
    assert "No reward of this kind was claimed this year." in bento
