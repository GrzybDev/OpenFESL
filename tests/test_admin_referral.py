from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_registration, post_sign_in, select_value
from heroes import create, create_hero
from identity import IdentityProvider, form_token, sign_in
from living import import_catalogue, setting
from store import ROYAL_SOLDIER
from support import visible
from test_admin_heroes import KEEPER, WARDEN
from test_referral import UNCONFIRMED, account_id, level
from test_rewards import INVENTED

pytestmark = pytest.mark.anyio

REWARDS = "/admin/rewards/bfheroes"
STEP = f"{REWARDS}/referral/second"
SWITCH = f"{REWARDS}/referral/switch"


async def recruited(base: str, database_url: str) -> tuple[int, int]:
    """Make Quillfuse recruit an account through its link, whose hero reaches level 7."""
    await setting(database_url, UNCONFIRMED, "off")
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    named = "UPDATE account SET display_name = 'Quillfuse' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    ann = await account_id(database_url, "ann@example.test")
    async with httpx2.AsyncClient() as friend:
        await friend.get(f"{base}/bfheroes/ref/{ann}")
        await post_registration(friend, base, email="cat@example.test")
        await create(friend, base, "Hollyhock", faction="national", **{"class": "soldier"})
        await level(database_url, "Hollyhock", 7)
        # The recruit's next page sees the goal reached.
        await friend.get(f"{base}/bfheroes/rewards")
    return ann, await account_id(database_url, "cat@example.test")


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_change_a_step_and_the_switch_with_notes_that_players_and_the_log_see(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, {**INVENTED, 257: INVENTED[1439]})
    ann, cat = await recruited(base, database_url)
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=REWARDS)).text)
        overview = visible((await client.get(f"{base}{REWARDS}")).content)
        # As the search follows typing, and as its Find button sends it without scripts.
        typed = visible((await client.get(f"{base}{STEP}?recruit-q=boost")).content)
        withheld = visible((await client.get(f"{base}{STEP}?recruit-q=257")).content)
        found = {"csrf": token, "find": "1", "recruiter-q": "golden", "note": ""}
        searched = await client.post(f"{base}{STEP}", data=found)
        form = {
            "csrf": token,
            "level": "6",
            "days": "0",
            "recruiter-kind": "funds",
            "recruiter-amount": "3000",
            "recruiter-name": "imported",
            "recruit-kind": "item",
            "recruit-item": "257",
            "recruit-own": "days",
            "recruit-count": "1",
            "recruit-name": "imported",
            "note": "Fewer players reach 15 in 60 days",
        }
        refused = await client.post(f"{base}{STEP}", data=form)
        own = {"recruiter-name": "own", "recruiter-text-en": "Invented Funds Purse"}
        fixed = form | {"level": "12", "days": "60", "recruit-kind": "points"} | own
        fixed |= {"recruit-amount": "1000"}
        saved = visible(
            (await client.post(f"{base}{STEP}", data=fixed, follow_redirects=True)).content
        )
        asked = visible((await client.get(f"{base}{SWITCH}")).content)
        off = {"csrf": token, "enabled": "0", "note": "Pause while second accounts are checked"}
        switched = visible(
            (await client.post(f"{base}{SWITCH}", data=off, follow_redirects=True)).content
        )
        account = visible((await client.get(f"{base}/admin/accounts/{ann}")).content)
        recruit = visible((await client.get(f"{base}/admin/accounts/{cat}")).content)
        remove = f"{base}/admin/accounts/{cat}/referral/bfheroes/remove"
        note = {"csrf": token, "back": f"/admin/accounts/{ann}", "note": "Second account"}
        removed = visible((await client.post(remove, data=note, follow_redirects=True)).content)
        log = visible((await client.get(f"{base}/admin/audit")).content)
    async with httpx2.AsyncClient() as player:
        await post_sign_in(player, base, "ann@example.test", PASSWORD)
        page = visible((await player.get(f"{base}/bfheroes/recruit-a-friend")).content)

    assert "Recruit a Friend Runs: personal links record new recruits" in overview
    assert "level 15 within 60 days of sign-up Recruiter gets 3,500 Funds" in overview
    assert "Recruits in all 1 Rewards claimed this month 0 Rewards waiting 2" in overview
    assert "Invented XP Boost" in typed and "Invented Stamina Boost" in typed
    assert "No item matches “257”." in withheld
    assert searched.status_code == 200 and "Invented Golden Machete" in visible(searched.content)
    shown = visible(refused.content)
    assert refused.status_code == 400
    assert "The second goal must be above the first, level 7." in shown
    assert "The window is a whole number of days from 1 to 3,650." in shown
    assert "a gift of 3,000 Funds needs own text" in shown
    assert "Item 257 is withheld on this service" in shown
    assert "Step 3 saved: A hero at level 12 within 60 days of sign-up" in saved
    assert "Invented Funds Purse" in saved
    assert "Switch Recruit a Friend off?" in asked
    assert (
        "Recruit a Friend switched off." in switched and "Off: links record nothing new" in switched
    )
    assert "Recruits of Quillfuse · 1 Invented_Cat" in account
    assert "Step 2: Ready on the rewards page" in account
    assert "Recruited by Quillfuse on" in recruit
    assert "Invented_Cat is no longer Quillfuse's recruit." in removed
    assert await select_value(database_url, "SELECT count(*) FROM referral") == 0
    assert "Recruit removed Invented_Cat (recruit of Quillfuse) Details Second account" in log
    assert "Recruit a Friend switched off Recruit a Friend Details Pause while second" in log
    assert "Goal level 15 → 12; Recruiter: 3,500 Funds → 3,000 Funds" in log
    assert "Recruit a Friend is not running on this service" in page


async def test_moderators_read_recruit_a_friend_and_change_nothing(
    admin_provider: IdentityProvider, base: str, database_url: str
) -> None:
    ann, _ = await recruited(base, database_url)
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=REWARDS)).text)
        overview = (await client.get(f"{base}{REWARDS}")).text
        step = visible((await client.get(f"{base}{STEP}")).content)
        asked = await client.get(f"{base}{SWITCH}")
        sent = await client.post(
            f"{base}{SWITCH}", data={"csrf": token, "enabled": "0", "note": "x"}
        )
        account = (await client.get(f"{base}/admin/accounts/{ann}")).text

    assert "Recruit a Friend" in visible(overview.encode()) and SWITCH not in overview
    assert "Recruit a Friend is changed with the admin role." in step
    assert asked.status_code == 403 and sent.status_code == 403
    assert "Invented_Cat" in account and "/referral/bfheroes/remove" not in account
    assert 'id="account-referral-bfheroes"' in account
    assert await select_value(database_url, "SELECT count(*) FROM setting") == 1
