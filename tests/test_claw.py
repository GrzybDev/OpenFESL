import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Literal

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from identity import form_token
from living import ROYAL, import_catalogue, setting
from playwright.async_api import Browser, Page, Request
from store import ROYAL_SOLDIER
from support import once_token, visible

from openfesl.core.drops import Box
from openfesl.core.store import Product

pytestmark = pytest.mark.anyio
CLAW = "/bfheroes/claw"
# A tonic of the common prizes, a Royal soldier's rifle of the uncommon and a Royal pet of the
# rare; the Claw's data names more, which the invented catalogue lacks.
ITEMS = {
    2153: ("Invented Tonic", "ability", {"consumable": "true"}),
    3015: ("Invented Rifle", "weapon", {"faction": ROYAL, "kits": "2"}),
    521: ("Invented Monkey", "appearance", {"faction": ROYAL, "slot": "8"}),
}
TEXTS = {"WEST_Custom_DailySpinner": "Invented Claw"}


def rarest(box: Box, may_get: Callable[[Product], bool], random: Random) -> Product | None:
    """Give the first prize of the last tier a hero may get anything of, in place of chance."""
    tiers = sorted({drop.tier for drop in box.drops}, reverse=True)
    for tier in tiers:
        found = [d.product for d in box.drops if d.tier == tier and may_get(d.product)]
        if found:
            return found[0]
    return None


async def focused(page: Page, selector: str) -> bool:
    """Return whether the element with the focus matches a selector."""
    return bool(await page.evaluate("s => document.activeElement.matches(s)", selector))


async def over_rewards(
    edge: Browser, base: str, client: httpx2.AsyncClient, motion: Literal["reduce", "no-preference"]
) -> Page:
    """Open the rewards page, which the Claw opens over, in a browser signed in as a client."""
    context = await edge.new_context(locale="en-GB", reduced_motion=motion)
    for name, value in client.cookies.items():
        await context.add_cookies([{"name": name, "value": value, "url": base}])
    page = await context.new_page()
    await page.goto(f"{base}/bfheroes/rewards")
    await page.locator("#claw-overlay .store-hero input").first.wait_for(state="attached")
    return page


async def claw_form(
    client: httpx2.AsyncClient, base: str, hero: int, paid: bool = False
) -> dict[str, str]:
    """Return the fields that the Claw's page sends with its Grab button for a hero."""
    page = (await client.get(f"{base}{CLAW}")).text
    form = {"csrf": form_token(page), "hero": str(hero), "once": once_token(page)}
    # The Claw's price follows the frame's slot purchase, which names a price too.
    shown = dict(re.findall(r'name="(price|currency)" value="([^"]*)"', page))
    return form | (shown | {"paid": "1"} if paid else {})


async def grab(client: httpx2.AsyncClient, base: str, form: dict[str, str]) -> str:
    """Send the Claw's form, and return the page it leads to."""
    answer = await client.post(f"{base}{CLAW}", data=form)
    assert answer.status_code == 303
    return visible((await client.get(base + answer.headers["location"])).content)


async def test_a_day_at_the_claw_gives_the_free_grab_once_and_sells_the_next(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("openfesl.core.drops.pick", rarest)
    await import_catalogue(server_url, tmp_path, capsys, ITEMS, TEXTS)
    royal = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Tin_Tankard", faction="national")
    named = "UPDATE account SET display_name = 'Quillfuse' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    async with httpx2.AsyncClient() as ann, httpx2.AsyncClient() as window:
        await post_sign_in(ann, base, "ann@example.test", PASSWORD)
        await post_sign_in(window, base, "ann@example.test", PASSWORD)
        front = (await ann.get(f"{base}/bfheroes")).text
        launcher = (await ann.get(f"{base}/bfheroes/play-now")).text
        page = visible((await ann.get(f"{base}{CLAW}")).content)
        # The close of the overlay keeps it from opening by itself again that day.
        await ann.post(f"{base}{CLAW}/close", data={"csrf": form_token(front), "next": "/bfheroes"})
        closed = (await ann.get(f"{base}/bfheroes")).text
        # A grab without its one-time token takes nothing.
        sent = await claw_form(ann, base, royal)
        del sent["once"]
        tokenless = await grab(ann, base, sent)
        # Two free grabs at once, as from two windows, give once.
        forms = [await claw_form(c, base, royal) for c in (ann, window)]
        answers = await asyncio.gather(
            *(
                client.post(f"{base}{CLAW}", data=form)
                for client, form in zip((ann, window), forms, strict=True)
            )
        )
        assert all(answer.status_code == 303 for answer in answers)
        pages = [visible((await c.get(f"{base}{CLAW}")).content) for c in (ann, window)]
        won = next(shown for shown in pages if "Prize for Brassbuckle" in shown)
        refused = next(shown for shown in pages if "Prize for Brassbuckle" not in shown)
        form = await claw_form(ann, base, royal, paid=True)
        paid = await grab(ann, base, form)
        # The form sent again, as Back and sending it do, takes and pays nothing more.
        again = await grab(ann, base, form)
        await execute(database_url, "UPDATE wallet SET balance = 100 WHERE currency = '_PF'")
        short = await grab(ann, base, await claw_form(ann, base, royal, paid=True))
        after = visible((await ann.get(f"{base}{CLAW}")).content)
        # The admin changes the price while a page shows the old one.
        stale = await claw_form(ann, base, royal, paid=True)
        await setting(database_url, "bfheroes.claw.paid.price", "50")
        repriced = await grab(ann, base, stale)

    assert "data-claw-overlay" in front and "data-opens-now" in front
    assert "Invented Claw" in visible(front.encode()) and "1 free grab waits" in front
    assert "data-claw-overlay" in launcher and "data-opens-now" not in launcher
    assert "data-opens-now" not in closed and "1 free grab waits" in closed
    assert "Nothing was taken: the form expired. The Claw is shown again." in tokenless
    assert "A free grab waits today. The prize goes to the hero picked here." in page
    assert "Chances for Brassbuckle" in page and "Chances for Tin_Tankard" in page
    assert "Rare 5% in all Invented Monkey · Permanent 5%" in page
    assert "Common 70% in all Invented Tonic · 50 uses 70%" in page
    assert "Chances for Tin_Tankard Common 100% in all Invented Tonic · 50 uses 100%" in page
    shown = "Invented Monkey Clothing · Permanent · a rare prize Brassbuckle owns it for good."
    assert shown in won
    assert "Battlefield Heroes shows it at Brassbuckle's next login." in won
    assert "Brassbuckle got Invented Monkey Permanent" in won
    assert re.search(r"Nothing was taken: today's free grab was used at \d\d:\d\d UTC", refused)
    assert "in another window, for Brassbuckle" in refused
    assert "Next free grab in" in won and "Grab again for 599 Funds" in won
    assert "Invented Monkey is left out: Brassbuckle owns it for good" in won
    assert "Invented Rifle Weapon · 7 days Brassbuckle has it for 7 days." in paid
    assert "This grab was made already; sending it again took no further prize." in again
    assert "Nothing was taken: the account has 100 Funds; a grab costs 599 Funds" in short
    assert "The account has 100 Funds; a grab costs 599 Funds (499 Funds short)." in after
    assert "Nothing was taken: the price of a grab changed to 50 Funds. Nothing" in repriced
    grabs = "SELECT count(*) FROM claw_grab WHERE persona_id = :hero"
    assert await select_value(database_url, grabs, hero=royal) == 2
    owned = "SELECT count(*) FROM entitlement WHERE persona_id = :hero AND tag LIKE '521-%'"
    assert await select_value(database_url, owned, hero=royal) == 1
    bought = "SELECT price FROM purchase WHERE kind = 'claw' AND persona_id = :hero"
    assert await select_value(database_url, bought, hero=royal) == 599
    funds = "SELECT delta FROM wallet_entry WHERE reason = 'purchase'"
    assert await select_value(database_url, funds) == -599


async def test_the_claw_says_why_it_takes_no_grab(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hero = await create_hero(base, "ann@example.test", "Pip-Lantern", faction="national")
    async with httpx2.AsyncClient() as visitor, httpx2.AsyncClient() as bob:
        before = visible((await visitor.get(f"{base}{CLAW}")).content)
        menu = visible((await visitor.get(f"{base}/bfheroes")).content)
        # Without the tonic, a National hero may win nothing.
        royal_only = {number: item for number, item in ITEMS.items() if number != 2153}
        await import_catalogue(server_url, tmp_path, capsys, royal_only)
        signed_out = visible((await visitor.get(f"{base}{CLAW}")).content)
        listed = visible((await visitor.get(f"{base}/bfheroes")).content)
        await post_sign_in(bob, base, "bob@example.test", PASSWORD)
        no_hero = (await bob.get(f"{base}/bfheroes")).text
        empty = visible((await bob.get(f"{base}{CLAW}")).content)
        moved = await bob.get(f"{base}/bfheroes/spinner")
    async with httpx2.AsyncClient() as ann:
        await post_sign_in(ann, base, "ann@example.test", PASSWORD)
        form = await claw_form(ann, base, hero)
        await setting(database_url, "bfheroes.claw.paid.enabled", "off")
        nothing = await ann.post(f"{base}{CLAW}", data=form)
        shown = visible((await ann.get(base + nothing.headers["location"])).content)
        await execute(
            database_url,
            "INSERT INTO claw_grab (game, account_id, persona_id, day, free, item_id, ownership,"
            " rare) SELECT 'bfheroes', account_id, id, now()::date, 1, 2153, 'x50', false"
            " FROM persona WHERE id = :hero",
            hero=hero,
        )
        paid_off = visible((await ann.get(f"{base}{CLAW}")).content)
        await setting(database_url, "bfheroes.claw.free", "2")
        second = visible((await ann.get(f"{base}{CLAW}")).content)
        await setting(database_url, "bfheroes.claw.enabled", "off")
        off = visible((await ann.get(f"{base}{CLAW}")).content)
        gone = visible((await ann.get(f"{base}/bfheroes")).content)
        late = await ann.post(f"{base}{CLAW}", data=form)
        said = visible((await ann.get(base + late.headers["location"])).content)

    assert "The Daily Claw needs the game's catalogue." in before
    assert "Daily claw machine" not in menu
    assert "Sign in to grab" in signed_out and "Create an account" in signed_out
    assert "Daily claw machine" in listed and "No rare prizes yet." in signed_out
    assert "data-opens-now" in no_hero
    assert "The Claw needs a hero" in empty and "Create a hero" in empty
    assert moved.status_code == 301 and moved.headers["location"] == CLAW
    assert "Nothing was taken: the Claw holds no prize for this hero now." in shown
    assert "1 free grab a day; paid grabs are switched off on this service." in paid_off
    assert "2 free grabs a day, from" in second and "Grab · free" in second
    assert "The Daily Claw is not running on this service." in off
    assert "Daily claw machine" not in gone
    assert "Nothing was taken: the Claw was switched off a moment ago." in said
    grabs = "SELECT count(*) FROM claw_grab WHERE persona_id = :hero"
    assert await select_value(database_url, grabs, hero=hero) == 1


@pytest.mark.usefixtures("scripts")
async def test_the_claw_over_a_page_keeps_its_hero_and_the_focus_through_grabs_and_closes(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, ITEMS, TEXTS)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Tin_Tankard", faction="national")
    async with httpx2.AsyncClient() as ann:
        await post_sign_in(ann, base, "ann@example.test", PASSWORD)
        # Reduced motion starts with Speed Claw, which shows the prize at once.
        page = await over_rewards(edge, base, ann, "reduce")
        await page.keyboard.press("Space")
        picked = await focused(page, "[data-claw-grab]")
        await page.locator("#claw-overlay [data-claw-change]").click()
        await page.keyboard.press("Space")
        kept_by_space = await focused(page, "[data-claw-grab]")
        await page.keyboard.press("Enter")
        await page.locator("dialog[data-claw-prize][open]").wait_for()
        await page.keyboard.press("Escape")
        await page.locator("dialog[data-claw-prize]").wait_for(state="detached")
        kept = await page.locator("#claw-overlay [data-claw-form]").get_attribute("data-hero")
        belts = await page.locator("#claw-overlay [data-belts]:not([hidden])").count()
        after_prize = await focused(page, "[data-claw-paid]")
        await page.keyboard.press("Escape")
        closed = await page.locator(".claw-closed").inner_text()
        await page.locator("summary.site-tab", has_text="store").click()
        await page.locator(".site-entry[data-claw-open]").click()
        await page.locator("#claw-overlay[open]").wait_for()
        await page.keyboard.press("Escape")
        await page.locator("#claw-overlay:not([open])").wait_for(state="attached")
        back = await focused(page, "summary.site-tab")
        await page.goto(f"{base}{CLAW}")
        await page.locator("summary.site-tab", has_text="store").click()
        async with page.expect_navigation():
            await page.locator(".site-entry[aria-current=page]").click()
        claws = await page.locator("[data-claw-form]").count()
        await page.context.close()

        await execute(database_url, "DELETE FROM claw_grab")
        page = await over_rewards(edge, base, ann, "no-preference")
        # A browser's clock a day ahead of the server's leaves the countdown to the next free grab
        # running, rather than loading the Claw again and again.
        await page.clock.set_system_time(datetime.now(UTC) + timedelta(days=1))
        sent: list[Request] = []
        # Playwright marks its handlers, which a list's bound append cannot be.
        page.on("request", lambda request: sent.append(request))
        await page.locator("#claw-overlay .store-hero").first.click()
        await page.locator("#claw-overlay [data-claw-grab]").click()
        # Closed while the machine plays, the Claw takes the answer in without its prize card.
        await page.keyboard.press("Escape")
        answered = page.locator("#claw-overlay [data-claw-form]:not([data-sent])")
        await answered.wait_for(state="attached")
        modal = await page.evaluate("!!document.querySelector(':modal')")
        await page.wait_for_timeout(500)
        await page.context.close()

    assert picked and kept_by_space
    assert kept and belts == 1 and after_prize
    assert "does not open by itself again in this browser before 00:00 UTC" in closed
    assert back and claws == 1
    assert not modal
    # The page that answers the grab is the one load of the Claw.
    assert [request.method for request in sent if request.url.endswith(CLAW)] == ["POST", "GET"]
