from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from accounts import execute, select_value
from heroes import create_hero
from identity import IdentityProvider, form_token, sign_in
from playwright.async_api import Browser
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER, stock
from support import visible

from openfesl.core.admins import Admin, Role
from openfesl.core.hero_admin import TooManyUses
from openfesl.core.inventory import COUNTED, Ownership
from openfesl.core.personas import Persona
from openfesl.storage.hero_admin import SqlHeroAdmin

pytestmark = pytest.mark.anyio
WARDEN = {"sub": "warden-7", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}
KEEPER = {"sub": "keeper-7", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def account_of(database_url: str, address: str) -> int:
    """Return the number of the account with a login name."""
    found = await select_value(
        database_url, "SELECT id FROM account WHERE nuid = :nuid", nuid=address
    )
    assert isinstance(found, int)
    return found


@pytest.mark.usefixtures("prompt_actions")
async def test_the_panel_lists_an_accounts_heroes_and_shows_each_heroes_page(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Mudlark")
    ann = await account_of(database_url, "ann@example.test")
    account = f"{base}/admin/accounts/{ann}"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        page = visible((await sign_in(client, base, next=f"/admin/accounts/{ann}")).content)
        # Heroes leave the in-game names for a list of their own, the first one the default.
        assert "In-game names 0" in page and "Heroes Battlefield Heroes 2" in page
        assert "2 of 4 used 4 free + 0 bought" in page
        assert f"Brassbuckle Royal Army Soldier · hero {brass}" in page
        assert "Default hero" in page and "0 unspent of 0 earned" in page
        assert "0 keys held by the account" in page
        today = datetime.now(UTC)
        day = f"{today.day} {today:%b %Y}"
        assert f"Created {day}" in page

        hero = visible((await client.get(f"{account}/heroes/{brass}")).content)
        assert "Faction Royal Army" in hero and "Class Soldier" in hero
        assert "Default hero Yes" in hero
        # The loadout that the game grants itself, and the bandages the site granted.
        assert (
            "Invented Bandage item 2005 Kind Abilities 10 uses left From Starting kit Active"
            in hero
        )
        assert "No portrait from the game yet" in hero
        ended = visible((await client.get(f"{account}/heroes/{brass}?items=ended")).content)
        assert "No item of the hero has ended." in ended

        polish = visible(
            (await client.get(f"{base}/pl/admin/accounts/{ann}/heroes/{brass}")).content
        )
        assert "Ekwipunek" in polish and "Zestaw startowy" in polish

        bob = await account_of(database_url, "bob@example.test")
        assert (await client.get(f"{base}/admin/accounts/{bob}/heroes/{brass}")).status_code == 404
        none = visible((await client.get(f"{base}/admin/accounts/{bob}")).content)
        assert "No hero yet." in none


async def changed(client: httpx2.AsyncClient, address: str, **form: str) -> str:
    """Send a hero's change with the form token of its page, and return the page it leads to."""
    page = await client.get(address)
    sent = await client.post(address, data={"csrf": form_token(page.text), **form})
    assert sent.status_code == 303, sent.text
    return visible((await client.get(sent.url.join(sent.headers["location"]))).content)


@pytest.mark.usefixtures("prompt_actions")
async def test_moderators_rename_and_disable_heroes_and_admins_choose_the_default(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Mudlark")
    ann = await account_of(database_url, "ann@example.test")
    hero = f"{base}/admin/accounts/{ann}/heroes/{brass}"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"/admin/accounts/{ann}")
        # A name another hero holds, in other capitals, is refused as it is typed and when sent.
        check = visible(
            (await client.get(f"{hero}/rename/check", params={"name": "MUDLARK"})).content
        )
        assert "MUDLARK is taken." in check
        page = await client.get(f"{hero}/rename")
        token = form_token(page.text)
        taken = await client.post(
            f"{hero}/rename", data={"csrf": token, "name": "mudlark", "note": "Asked by mail"}
        )
        assert taken.status_code == 400 and "Still needed: a free name." in visible(taken.content)
        unnoted = await client.post(f"{hero}/rename", data={"csrf": token, "name": "Copper_Kettle"})
        assert "Still needed: a note." in visible(unnoted.content)
        renamed = await changed(client, f"{hero}/rename", name="Copper_Kettle", note="Ticket 59")
        assert "Renamed Brassbuckle to Copper_Kettle." in renamed
        # The public profile shows once the account has a display name, while the hero is enabled.
        unnamed = "No public profile: profiles show the heroes in play of named accounts that can"
        assert unnamed in renamed
        named = "UPDATE account SET display_name = 'Quill_Invented' WHERE id = :id"
        await execute(database_url, named, id=ann)
        profile = f'href="/bfheroes/heroes/{brass}"'
        assert profile in (await client.get(hero)).text

        # Disabling the default hero names what it does, and the default moves to the next one.
        asked = visible((await client.get(f"{hero}/disable")).content)
        assert "Keeps its slot: 2 of 4 slots stay used." in asked
        assert "The default moves to Mudlark." in asked
        disabled = await changed(client, f"{hero}/disable", note="Cheating report 88")
        assert "Copper_Kettle is disabled. Mudlark is now the default hero." in disabled
        assert "by invented-warden: Cheating report 88" in disabled
        assert "A disabled hero cannot be the default." in disabled
        assert unnamed in disabled
        enabled = await changed(client, f"{hero}/enable", note="Appeal accepted")
        assert "enabled again. The default hero stays Mudlark." in enabled
        assert (await client.post(f"{hero}/default", data={"csrf": token})).status_code == 403

    admin_provider.user = KEEPER
    await execute(
        database_url,
        "INSERT INTO mugshot (persona_id, version, image) VALUES (:hero, 3, :image)",
        hero=brass,
        image=b"\x89PNG invented",
    )
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"/admin/accounts/{ann}")
        made = await changed(client, f"{hero}/default", note="Asked for in ticket 60")
        assert "Copper_Kettle is now the default hero." in made
        assert (
            await client.get(f"{base}/admin/mugshots/bfheroes/{brass}/3.png")
        ).status_code == 200
        removed = await changed(client, f"{hero}/portrait", note="Offensive image")
        assert "Removed the portrait of Copper_Kettle." in removed
        assert (
            await client.get(f"{base}/admin/mugshots/bfheroes/{brass}/3.png")
        ).status_code == 404
        log = visible((await client.get(f"{base}/admin/audit", params={"account": ann})).content)
        for line in (
            "Portrait removed Copper_Kettle",
            "Made default Copper_Kettle",
            "was Mudlark · Asked for in ticket 60",
            "Hero enabled Copper_Kettle",
            "Hero disabled Copper_Kettle",
            "default moved to Mudlark · Cheating report 88",
            "Hero renamed Copper_Kettle",
            "Brassbuckle → Copper_Kettle · Ticket 59",
        ):
            assert line in log


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_grant_change_and_revoke_items_that_fit_the_hero(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    await stock(server_url, tmp_path, capsys)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    ann = await account_of(database_url, "ann@example.test")
    hero = f"{base}/admin/accounts/{ann}/heroes/{brass}"
    grant = f"{hero}/items/grant"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"/admin/accounts/{ann}")
        assert (await client.get(grant)).status_code == 403
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"/admin/accounts/{ann}")
        # The search finds items by name; one for another faction and class is refused.
        found = visible((await client.get(grant, params={"q": "rifle"})).content)
        assert "2 items match." in found and "Invented Longrifle" in found
        # A number longer than any item's finds nothing.
        endless = await client.get(grant, params={"q": "9" * 5000})
        assert "No item matches" in visible(endless.content)
        unfit = await client.get(grant, params={"q": "rifle", "item": "3042"})
        assert "Does not fit" in visible(unfit.content) and "Requires: National Army" in unfit.text
        token = form_token(unfit.text)
        refused = await client.post(
            grant, data={"csrf": token, "item": "3042", "ownership": "perm", "note": "Prize"}
        )
        assert "Not granted: Brassbuckle cannot use Invented Longrifle." in visible(refused.content)
        # A rental, then more days of it, and the withheld shirt, which only admins grant.
        rented = await changed(client, grant, item="3015", ownership="7d", note="Event prize")
        assert "Granted Invented Rifle to Brassbuckle: Ends" in rented
        longer = visible(
            (await client.get(grant, params={"item": "3015", "ownership": "3d"})).content
        )
        assert "Adds 3 days: ends" in longer
        await changed(client, grant, item="3015", ownership="3d", note="Event prize, part two")
        shirt = await changed(client, grant, item="257", ownership="perm", note="Contest winner")
        assert "Invented Logo Shirt item 257 · granted by hand only" in shirt
        # Uses add to those the hero has; a base item is held for good.
        uses = visible((await client.get(grant, params={"item": "2005", "uses": "25"})).content)
        assert "Adds 25 uses: 10 → 35 uses left." in uses
        await changed(client, grant, item="2005", ownership="perm", uses="25", note="Outage")
        most = {"csrf": token, "item": "2005", "uses": "2147483647", "note": "Typo"}
        over = await client.post(grant, data=most)
        assert over.status_code == 400
        assert (
            "Not possible: there would be 2,147,483,682 uses, more than the game reads."
            in visible(over.content)
        )
        # A grant at the same time is bounded again under the item's lock.
        engine = create_async_engine(database_url)
        persona = Persona(brass, ann, "bfheroes", "Brassbuckle")
        with pytest.raises(TooManyUses):
            await SqlHeroAdmin(async_sessionmaker(engine)).grant_item(
                persona, 2005, Ownership(COUNTED, uses=2**31 - 1), "Race", Admin("", "", Role.ADMIN)
            )
        await engine.dispose()
        held = await client.post(
            grant, data={"csrf": token, "item": "1", "ownership": "perm", "note": "Twice"}
        )
        assert "Brassbuckle holds Invented Hat for good" in visible(held.content)

        fewer = await changed(client, f"{hero}/items/2005/change", value="3", note="Counted twice")
        assert "Changed Invented Bandage of Brassbuckle." in fewer
        assert "Invented Bandage item 2005 Kind Abilities 3 uses left Running low" in fewer
        revoked = await changed(client, f"{hero}/items/3015/revoke", note="Granted twice")
        assert "Revoked Invented Rifle from Brassbuckle. Nothing was refunded." in revoked
        ended = visible((await client.get(hero, params={"items": "ended"})).content)
        assert "Invented Rifle item 3015 Kind Weapons" in ended
        assert "From Admin Revoked" in ended and "Grant again" in ended
        log = visible((await client.get(f"{base}/admin/audit", params={"account": ann})).content)
        assert "Item revoked Brassbuckle" in log and "Invented Rifle · Granted twice" in log
        assert "Invented Bandage, 35 uses left → 3 uses left · Counted twice" in log
        context = await edge.new_context(locale="en-GB")
        for name, value in client.cookies.items():
            await context.add_cookies([{"name": name, "value": value, "url": base}])
    # With scripts, the grant's own button waits for the note, not the search's inside its form.
    page = await context.new_page()
    await page.goto(f"{grant}?item=3015")
    granting, note = page.get_by_role("button", name="Grant item"), page.locator("#admin-note")
    await note.fill("Prize")
    await note.fill("")
    assert await granting.get_attribute("aria-disabled") == "true"
    await note.fill("Prize")
    assert await granting.get_attribute("aria-disabled") == "false"
    await context.close()
