import asyncio
import re
from pathlib import Path

import httpx2
import pytest
from accounts import (
    PASSWORD,
    execute,
    post_registration,
    post_sign_in,
    select_value,
    sign_in_through,
)
from heroes import create, create_hero, start
from identity import IdentityProvider, form_token
from living import import_catalogue, setting
from store import ROYAL_SOLDIER
from support import FESL, THEATER, game, let_in, update_stats, visible
from test_player_servers import SERVER_GAME, until_listed
from test_rewards import INVENTED

pytestmark = pytest.mark.anyio

REWARDS = "/bfheroes/rewards"
CLAIM = "/bfheroes/rewards/claim"
# The game's offer of the first step, invented, which states its amount, and one of the second
# step that states another, as an older copy's did.
OFFERS = {
    "WEST_Custom_LevelOfferAffiliate": "Invented 1350 Valor Award",
    "WEST_Custom_FundingOfferAffiliate": "Invented 700 Funds Offer",
}
RECRUITS = "SELECT count(*) FROM referral"
# Without mail the site registers accounts only while they need no confirmed address.
UNCONFIRMED = "site.accounts.email_verification"


async def account_id(database_url: str, address: str) -> int:
    found = await select_value(database_url, "SELECT id FROM account WHERE nuid = :a", a=address)
    assert isinstance(found, int)
    return found


async def level(database_url: str, hero: str, value: int) -> None:
    """Set the level of a hero, as a dedicated server's stats do."""
    await execute(
        database_url,
        "UPDATE stat SET value = :value WHERE key = 'level'"
        " AND owner_id = (SELECT id FROM persona WHERE name = :hero)",
        value=value,
        hero=hero,
    )


async def balance(database_url: str, address: str, currency: str) -> object:
    return await select_value(
        database_url,
        "SELECT balance FROM wallet JOIN account ON account.id = wallet.account_id"
        " WHERE nuid = :address AND currency = :currency",
        address=address,
        currency=currency,
    )


async def claim(client: httpx2.AsyncClient, base: str, key: str, hero: str = "") -> None:
    page = await client.get(f"{base}{REWARDS}")
    form = {"csrf": form_token(page.text), "key": key, "faction": "", "hero": hero}
    await client.post(f"{base}{CLAIM}", data=form)


@pytest.mark.usefixtures("prompt_actions")
async def test_a_link_makes_a_new_account_a_recruit_whose_steps_each_account_claims_once(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED, OFFERS)
    await setting(database_url, UNCONFIRMED, "off")
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    named = "UPDATE account SET display_name = 'Quillfuse' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    ann = await account_id(database_url, "ann@example.test")
    async with httpx2.AsyncClient() as friend, httpx2.AsyncClient() as recruiter:
        landing = visible((await friend.get(f"{base}/bfheroes/ref/{ann}")).content)
        form = visible((await friend.get(f"{base}/register")).content)
        made = await post_registration(friend, base, email="cat@example.test")
        assert made.status_code == 303, made.text
        # The recruit's first hero is below the first step's level, its second reaches it.
        await create(friend, base, "Cinderpatch", faction="royal", **{"class": "commando"})
        await create(friend, base, "Hollyhock", faction="national", **{"class": "soldier"})
        waiting = visible((await friend.get(f"{base}{REWARDS}")).content)
        await level(database_url, "Hollyhock", 7)
        ready = await friend.get(f"{base}{REWARDS}")
        picking = visible(
            (
                await friend.get(f"{base}{CLAIM}", params={"key": "referral:first", "faction": ""})
            ).content
        )
        hero = "SELECT id FROM persona WHERE name = 'Hollyhock'"
        hollyhock = await select_value(database_url, hero)
        await claim(friend, base, "referral:first", str(hollyhock))
        boosted = visible((await friend.get(f"{base}{REWARDS}")).content)
        # A hero that the admin took out of play leads no step, however high its level.
        await level(database_url, "Cinderpatch", 9)
        off_play = "UPDATE persona SET state = 'disabled' WHERE name = 'Cinderpatch'"
        await execute(database_url, off_play)

        await post_sign_in(recruiter, base, "ann@example.test", PASSWORD)
        cat = await account_id(database_url, "cat@example.test")
        first, second = f"referral:{cat}:first", f"referral:{cat}:second"
        page = await recruiter.get(f"{base}{REWARDS}")
        offered = visible(page.content)
        # Two claims at once, as from two tabs, give once.
        await asyncio.gather(*(claim(recruiter, base, first) for _ in range(2)))
        await level(database_url, "Hollyhock", 15)
        await claim(recruiter, base, second)
        counted = await recruiter.get(f"{base}{REWARDS}")
        claimed = visible(counted.content)
        polish = visible((await friend.get(f"{base}/pl{REWARDS}")).content)
        # A recruiter that other accounts no longer see, as one deactivated, goes unnamed.
        gone = "UPDATE account SET state = 'deactivated' WHERE nuid = 'ann@example.test'"
        await execute(database_url, gone)
        unnamed = visible((await friend.get(f"{base}{REWARDS}")).content)

    assert "Sign up to play with Quillfuse Default hero Brassbuckle" in landing
    assert "This registration came through Quillfuse's personal link" in form
    assert await select_value(database_url, RECRUITS) == 1
    assert "Recruited by Quillfuse · step 2" in waiting
    assert "When a hero of this account reaches level 7." in waiting
    assert "Locked until then · no deadline" in waiting
    assert "Recruited by Quillfuse · step 2" in visible(ready.content)
    assert "Ready to claim · no deadline" in visible(ready.content)
    # The gift is the game data's, but the copy's text of it was never imported: its own words.
    assert "Invented XP Boost · 1 day" in picking
    assert "Level 1: it goes to a hero of level 7 or higher" in picking
    held = "SELECT count(*) FROM entitlement WHERE tag = '9000-tlmd' AND persona_id = :hero"
    assert await select_value(database_url, held, hero=hollyhock) == 1
    assert "Claimed on" in boosted and "for Hollyhock" in boosted
    # The imported offer states the first gift; the second's states another amount.
    assert "Recruit Invented_Cat · step 2 Invented 1350 Valor Award" in offered
    assert "Invented_Cat's hero Hollyhock reached level 7 on" in offered
    assert "Recruit Invented_Cat · step 3 3,500 Funds" in offered
    assert "Hollyhock Level 7 of 15" in offered and "Cinderpatch" not in offered
    # Valor Points need no hero, so Claim sends at once rather than asking for one.
    assert f"key=referral%3A{cat}%3Afirst" not in page.text
    assert await balance(database_url, "ann@example.test", "_DV") == 1350
    given = "SELECT count(*) FROM reward_claim WHERE key = :key"
    assert await select_value(database_url, given, key=first) == 1
    # The Funds wallet opened with its allowance of 2,500.
    assert await balance(database_url, "ann@example.test", "_PF") == 2500 + 3500
    assert "Claimed this year · 2" in claimed
    # The bar counts the Funds up from what the account had before the claim.
    assert re.search(r'data-count="6000"\s+data-count-from="2500"', counted.text)
    # The recruit's second step was reached too and waits for it.
    assert "Zaproszenie od: Quillfuse · krok 3" in polish
    assert "Do odebrania · bez terminu" in polish
    assert "Quillfuse" not in unnamed and "Recruited · step 3" in unnamed
    assert "This account was recruited through Recruit a Friend." in unnamed


@pytest.mark.usefixtures("open_hosting")
async def test_a_level_a_server_writes_in_time_keeps_the_step_when_no_page_sees_it_in_time(
    base: str, server_url: str, database_url: str
) -> None:
    await setting(database_url, UNCONFIRMED, "off")
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    named = "UPDATE account SET display_name = 'Quillfuse' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    ann = await account_id(database_url, "ann@example.test")
    await create_hero(base, "bob@example.test", "Tin_Tankard", **ROYAL_SOLDIER)
    async with httpx2.AsyncClient() as friend:
        await friend.get(f"{base}/bfheroes/ref/{ann}")
        await post_registration(friend, base, email="cat@example.test")
        await create(friend, base, "Hollyhock", faction="national", **{"class": "soldier"})
    async with game(server_url) as cat, game(server_url) as server:
        _, _, hero = await start(server_url, cat, "cat@example.test")
        await start(server_url, server, "bob@example.test", role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        await until_listed(cat)
        await let_in(server, cat, created.fields)
        leveled = update_stats((str(hero), "1", [(0, "level", "15.0000", "", "0")]))
        assert "errorCode" not in await server.request(FESL, "rank", leveled)
    # The second step's 60 days end before either account opens a page.
    await execute(database_url, "UPDATE referral SET created_at = now() - interval '61 days'")
    async with httpx2.AsyncClient() as friend, httpx2.AsyncClient() as recruiter:
        await post_sign_in(friend, base, "cat@example.test", PASSWORD)
        own = visible((await friend.get(f"{base}{REWARDS}")).content)
        await post_sign_in(recruiter, base, "ann@example.test", PASSWORD)
        offered = visible((await recruiter.get(f"{base}{REWARDS}")).content)

    assert "Hollyhock reached level 15 on" in own
    assert "this step can no longer be met" not in own
    assert "Invented_Cat's hero Hollyhock reached level 15 on" in offered


async def test_links_record_nobody_signed_in_off_or_unknown_and_a_window_ends_a_step(
    services: tuple[IdentityProvider, IdentityProvider], base: str, database_url: str
) -> None:
    connect, _ = services
    await setting(database_url, UNCONFIRMED, "off")
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    named = "UPDATE account SET display_name = 'Quillfuse' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    ann = await account_id(database_url, "ann@example.test")
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "bob@example.test", PASSWORD)
        bob_id = await account_id(database_url, "bob@example.test")
        unnamed = visible((await client.get(f"{base}/bfheroes/ref/{bob_id}")).content)
        unnamed_other = visible((await client.get(f"{base}/bfheroes/ref/{ann}")).content)
        bob = "UPDATE account SET display_name = 'Thimblerig' WHERE nuid = 'bob@example.test'"
        await execute(database_url, bob)
        other = visible((await client.get(f"{base}/bfheroes/ref/{ann}")).content)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        own = visible((await client.get(f"{base}/bfheroes/ref/{ann}")).content)
    async with httpx2.AsyncClient() as client:
        # A link of nobody, then of an account others do not see, record no one.
        unknown = visible((await client.get(f"{base}/bfheroes/ref/987654")).content)
        await execute(database_url, "UPDATE account SET state = 'banned' WHERE id = :id", id=ann)
        hidden = visible((await client.get(f"{base}/bfheroes/ref/{ann}")).content)
        await execute(database_url, "UPDATE account SET state = 'active' WHERE id = :id", id=ann)
        await setting(database_url, "bfheroes.rewards.referral.enabled", "off")
        off = visible((await client.get(f"{base}/bfheroes/ref/{ann}")).content)
        await post_registration(client, base, email="dot@example.test", display_name="Dotted_Line")
    assert await select_value(database_url, RECRUITS) == 0
    await setting(database_url, "bfheroes.rewards.referral.enabled", "on")
    async with httpx2.AsyncClient() as client:
        # A sign-in service's new identity becomes the recruit when its account is made.
        await client.get(f"{base}/bfheroes/ref/{ann}")
        connect.user = {"sub": "new-1", "name": "Wickerwing", "email": "wick@example.test"}
        await sign_in_through(client, base, "connect")
        step = visible((await client.get(f"{base}/register/service")).content)
        made = await post_registration(
            client, base, "/register/service", email="wick@example.test", display_name="Wickerwing"
        )
        assert made.status_code == 303, made.text
        await create(client, base, "Saltmarsh", faction="royal", **{"class": "gunner"})
        await level(database_url, "Saltmarsh", 9)
        # The second step's 60 days ended at level 9.
        await execute(database_url, "UPDATE referral SET created_at = now() - interval '61 days'")
        ended = visible((await client.get(f"{base}{REWARDS}")).content)

    assert "Personal links work for accounts with a display name" in unnamed
    assert "Choose a display name" in unnamed and "own personal link" not in unnamed
    assert "so a visit while signed in changes nothing" in unnamed_other
    assert "Signed in as Thimblerig." in other and "Sign up" not in other
    assert "This is Quillfuse's own personal link." in own
    for page in (unknown, hidden, off):
        assert "Battlefield Heroes on OpenFESL is free to play" in page
        assert "Sign up to play with" not in page
    assert "This registration came through Quillfuse's personal link" in step
    assert await select_value(database_url, RECRUITS) == 1
    # The first step has no deadline; its boost waits for a catalogue that says what fits.
    assert "Saltmarsh reached level 7 on" in ended
    assert "None of the account's heroes can take it." in ended
    assert "The 60 days after this account's sign-up ended on" in ended
    assert "at level 9; this step can no longer be met." in ended
