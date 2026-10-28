import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from heroes import wallets
from identity import form_token
from store import ROYAL_SOLDIER, grant, shopper
from support import visible

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
