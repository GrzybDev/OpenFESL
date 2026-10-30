import asyncio
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from identity import form_token
from living import ROYAL, freeze, import_catalogue, setting, worn
from sqlalchemy import event
from sqlalchemy.engine import Engine
from store import ROYAL_SOLDIER
from support import visible
from test_rewards import CHRISTMAS, INVENTED

pytestmark = pytest.mark.anyio

REWARDS = "/bfheroes/rewards"
CLAIM = "/bfheroes/rewards/claim"
CHESTS = "/bfheroes/treasure-chests"


@pytest.fixture
def christmas(monkeypatch: pytest.MonkeyPatch) -> None:
    freeze(monkeypatch, CHRISTMAS)


@pytest.mark.usefixtures("christmas", "prompt_actions")
async def test_the_rewards_page_shows_each_gift_and_gives_a_claimed_one_once_to_the_hero_picked(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    soldier = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    commando = await create_hero(
        base, "ann@example.test", "Cutthroat", faction="royal", **{"class": "commando"}
    )
    # Made a month before Christmas, as the database dates heroes by its own clock.
    await execute(database_url, "UPDATE persona SET created_at = '2026-11-01 12:00+00'")
    gift = {"key": "christmas:2026:5", "faction": "royal"}
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        before = await client.get(f"{base}{REWARDS}")
        # Without scripts the claim leads to a page with the claim form.
        picking = await client.get(f"{base}{CLAIM}", params=gift)
        form = {"csrf": form_token(before.text), **gift, "hero": str(commando)}
        await client.post(f"{base}{CLAIM}", data=form)
        claimed = await client.get(f"{base}{REWARDS}")
        account = {"csrf": form_token(before.text), "key": "milestone:first-hero", "faction": ""}
        # Two claims at once, as from two tabs, give once.
        await asyncio.gather(*(client.post(f"{base}{CLAIM}", data=account) for _ in range(2)))
        funded = await client.get(f"{base}{REWARDS}")
        polish = visible((await client.get(f"{base}/pl{REWARDS}")).content)

    shown = visible(before.content)
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
    # The menus count what waits: the Royal gift and the first hero's Funds.
    assert shown.count("2 rewards to claim") == 2
    # Each hero gets one roll of the box; the default hero is picked.
    assert visible(picking.content).count("Opens at once for this hero") == 2
    assert re.search(rf'value="{soldier}"[^>]* checked', picking.text)
    given = "SELECT count(*) FROM reward_claim WHERE key = 'milestone:first-hero'"
    assert await select_value(database_url, given) == 1
    after = visible(claimed.content)
    assert re.search(r"Claimed on 5 Dec for Cutthroat: Invented \w+ · ", after)
    assert "Box opened Item for Cutthroat" in after
    assert "Claimed this year · 1" in after
    money = visible(funded.content)
    assert "3,100 Funds" in money
    assert "rewards to claim" not in money and "Nothing to claim right now" in money
    assert "Świąteczne prezenty, dzień 5 z 24." in polish
    assert "Odebrane w tym roku · 2" in polish


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


async def test_the_first_chests_key_waits_on_the_chests_page_until_it_is_claimed(
    base: str, database_url: str
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    # A dedicated server stacks the account's first chest on its hero.
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, use_count, status) SELECT"
        " account_id, id, 'BFH_TREASURE_CHEST', 1, 'ACTIVE' FROM persona WHERE id = :hero",
        hero=hero,
    )
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        waiting = visible((await client.get(f"{base}{CHESTS}")).content)
        page = await client.get(f"{base}{REWARDS}")
        form = {"csrf": form_token(page.text), "key": "first-chest", "faction": ""}
        await client.post(f"{base}{CLAIM}", data=form)
        taken = visible((await client.get(f"{base}{CHESTS}")).content)

    assert "A first key for the account waits on the rewards page." in waiting
    assert "No keys" in waiting
    assert "waits on the rewards page" not in taken
    assert "1 key" in taken


@pytest.mark.usefixtures("christmas", "prompt_actions")
async def test_while_claims_are_paused_the_page_says_so_and_the_days_gifts_wait_for_later(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await setting(database_url, "bfheroes.rewards.paused", "on")
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        paused = await client.get(f"{base}{REWARDS}")
        form = {"csrf": form_token(paused.text), "key": "milestone:first-hero", "faction": ""}
        await client.post(f"{base}{CLAIM}", data=form)
        refused = visible((await client.get(f"{base}{REWARDS}")).content)
        await setting(database_url, "bfheroes.rewards.paused", "off")
        freeze(monkeypatch, CHRISTMAS + timedelta(days=1))
        resumed = visible((await client.get(f"{base}{REWARDS}")).content)

    shown = visible(paused.content)
    assert "Claims are paused for now" in shown
    assert re.search(r'aria-disabled="true"\s+aria-describedby="claim-christmas', paused.text)
    assert "Claims are paused; this reward waits." in shown
    assert "rewards to claim" not in shown and "Ready to claim 0" in shown
    assert "Nothing to claim right now" not in shown
    assert "Nothing was given: claims are paused for now." in refused
    # The paused fifth day's gift for Brassbuckle waits beside the sixth day's.
    assert "Day 5 of 24, kept from a paused day · until 24 Dec" in resumed
    assert "Day 6 of 24 · until 00:00 UTC" in resumed


@pytest.mark.usefixtures("prompt_actions")
async def test_a_gifts_box_reveals_what_it_gave_and_says_whether_the_hero_had_it(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Of Santa's Visit the Royal soldier may get only the rifle, for 30 days; its gift of the
    # 17th day is a hat the soldier owns for good, which turns into the rewards' tonics.
    invented = {
        3212: ("Invented Santa Rifle", "weapon", {"faction": ROYAL, "kits": "2"}),
        1530: worn("Invented Clover Hat", ROYAL, "8"),
    }
    await import_catalogue(server_url, tmp_path, capsys, invented)
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, status) SELECT account_id, id,"
        " '1530-unlm', 'ACTIVE' FROM persona WHERE id = :hero",
        hero=hero,
    )
    shown = []
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        for day in (5, 6, 17):
            freeze(monkeypatch, datetime(2026, 12, day, 12, tzinfo=UTC))
            page = await client.get(f"{base}{REWARDS}")
            gift = {"key": f"christmas:2026:{day}", "faction": "royal", "hero": str(hero)}
            await client.post(f"{base}{CLAIM}", data={"csrf": form_token(page.text), **gift})
            shown.append(visible((await client.get(f"{base}{REWARDS}")).content))

    new, added, own = shown
    assert "Box opened Item for Brassbuckle Invented Santa Rifle 30 days" in new
    assert "already had it" not in new
    assert "Brassbuckle already had it: 30 days added," in added
    # The gift gave its own item's tonics without rolling its box.
    assert "Box opened" not in own and "Claimed Christmas gifts: 2153 · 150 uses" in own


@pytest.mark.usefixtures("prompt_actions")
async def test_a_claim_that_gives_nothing_says_when_it_was_claimed_or_when_its_day_ended(
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze(monkeypatch, CHRISTMAS)
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}{REWARDS}")
        csrf = form_token(page.text)

        async def claim(day: int) -> str:
            gift = {"key": f"christmas:2026:{day}", "faction": "royal", "hero": str(hero)}
            await client.post(f"{base}{CLAIM}", data={"csrf": csrf, **gift})
            return visible((await client.get(f"{base}{REWARDS}")).content)

        await claim(5)
        # Claimed again from the same page, as from a second tab.
        again = await claim(5)
        freeze(monkeypatch, CHRISTMAS + timedelta(days=1))
        late = await claim(5)
        freeze(monkeypatch, datetime(2026, 12, 25, 12, tzinfo=UTC))
        over = await claim(24)

    assert (
        "Nothing was given: today's gift (Christmas gifts, Royal Army) was already claimed for"
        " Brassbuckle at 12:00 UTC." in again
    )
    assert "Nothing was given: day 5 of Christmas gifts ended at 00:00 UTC." in late
    assert "Nothing was given: Christmas gifts ended at 00:00 UTC." in over


@pytest.mark.usefixtures("prompt_actions")
async def test_each_signed_in_page_runs_the_rewards_once_and_takes_an_events_counts_once(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A day of Halloween, whose four missions count from the counts taken at its start.
    freeze(monkeypatch, datetime(2026, 10, 28, 14, tzinfo=UTC))
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    statements: list[str] = []

    def executed(*arguments: object) -> None:
        statements.append(str(arguments[2]))

    pages = (REWARDS, CHESTS, "/bfheroes/store/purchaseFunds", "/bfheroes", "/bfheroes/heroes")
    taken: dict[str, tuple[int, int]] = {}
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        await client.get(f"{base}{REWARDS}")
        event.listen(Engine, "before_cursor_execute", executed)
        try:
            for path in pages:
                statements.clear()
                await client.get(f"{base}{path}")
                baselines = [s for s in statements if "reward_baseline" in s]
                taken[path] = (len(baselines), sum(s.startswith("INSERT") for s in baselines))
        finally:
            event.remove(Engine, "before_cursor_execute", executed)

    assert taken == dict.fromkeys(pages, (4, 0))


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
