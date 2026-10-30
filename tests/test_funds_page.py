from datetime import UTC, datetime

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from heroes import create_hero, wallets
from identity import form_token
from living import freeze
from store import ROYAL_SOLDIER, grant, shopper
from support import visible
from test_rewards import CHRISTMAS

pytestmark = pytest.mark.anyio

FUNDS = "/bfheroes/store/purchaseFunds"


async def test_the_funds_page_shows_whose_balances_and_when_the_allowance_came(
    base: str, server_url: str, database_url: str
) -> None:
    named = "UPDATE account SET display_name = 'Ann_Invented' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{FUNDS}?from=game")).content)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        no_hero = await client.get(f"{base}{FUNDS}")
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await grant(database_url, hero, "_DH", 3)
    # The game loads the hero's wallets, which opens the account's with its allowance.
    await wallets(server_url, cookie, hero)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        from_game = await client.get(f"{base}{FUNDS}?from=game")
        polish = visible((await client.get(f"{base}/pl{FUNDS}")).content)
        # The game may log in with another account than the browser's, which signs out here.
        switch = {"csrf": form_token(from_game.text), "next": f"{FUNDS}?from=game"}
        switched = await client.post(f"{base}/sign-out", data=switch)

    assert "Battlefield Heroes sent this page" in signed_out
    assert "Account, all heroes." not in signed_out
    menu = visible(no_hero.content)
    assert "Battlefield Heroes sent this page" not in menu
    assert "Funds 2,500 Account, all heroes." in menu and "Valor Points 0" in menu
    assert "Hero Points No hero yet" in menu
    assert "got it on" not in menu
    shown = visible(from_game.content)
    assert "belong to Ann_Invented" in shown
    assert "Brassbuckle 3 unspent of 3" in shown
    assert "Ann_Invented got it on " in shown
    assert "Konto Ann_Invented otrzymało go " in polish
    assert switched.headers["location"] == (
        "/sign-in?next=%2Fbfheroes%2Fstore%2FpurchaseFunds%3Ffrom%3Dgame"
    )


async def test_the_funds_page_lists_the_rewards_with_funds_and_claims_one_where_it_is(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch, CHRISTMAS)
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    level = "UPDATE stat SET value = 10 WHERE owner_id = :hero AND key = 'level'"
    await execute(database_url, level, hero=hero)
    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{FUNDS}")).content)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}{FUNDS}")
        claim = {"csrf": form_token(page.text), "faction": "", "next": FUNDS}
        level_10 = {**claim, "key": f"milestone:level-10:{hero}"}
        claimed = await client.post(f"{base}/bfheroes/rewards/claim", data=level_10)
        after = visible((await client.get(f"{base}{FUNDS}")).content)
        first_hero = {**claim, "key": "milestone:first-hero"}
        await client.post(f"{base}/bfheroes/rewards/claim", data=first_hero)
        # A milestone is claimed once for good; the next year still says when.
        freeze(monkeypatch, datetime(2027, 1, 10, 12, tzinfo=UTC))
        next_year = visible((await client.get(f"{base}{FUNDS}")).content)
        rewards = visible((await client.get(f"{base}/bfheroes/rewards")).content)

    # Signed out the rewards are listed without anyone's progress.
    assert "Web rewards with Funds" in signed_out
    assert "Level 10 · for each hero" in signed_out and "+500 Funds a hero" in signed_out
    assert "Goal reached" not in signed_out
    shown = visible(page.content)
    assert "First hero Goal reached Claim" in shown and "+600 Funds" in shown
    assert "Brassbuckle Goal reached Claim" in shown
    # No hero reached level 20, so the one that came closest stands for them.
    assert "Level 20 · for each hero Best: Brassbuckle, Level 10 of 20" in shown
    assert claimed.headers["location"] == FUNDS
    assert "Claimed Level 10 · 500 Funds: 500 Funds for the account." in after
    assert "Funds 3,000 Account, all heroes." in after
    assert "Brassbuckle Claimed on 5 Dec 2026" in after
    assert "First hero Claimed on 5 Dec 2026" in next_year
    assert "Account Claimed on 5 Dec 2026" in rewards
