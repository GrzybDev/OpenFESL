import httpx2
import pytest
from accounts import PASSWORD, execute, select_value
from identity import form_token
from support import markup, visible

pytestmark = pytest.mark.anyio


async def test_a_game_site_signs_in_on_its_pages_and_shows_balances_without_opening_wallets(
    base: str, database_url: str
) -> None:
    named = "UPDATE account SET display_name = 'Ann_Invented' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}/bfheroes/play")
        shown = markup(page.content)
        # The site's menus list only the pages the game has.
        assert 'href="/bfheroes/play" aria-current="page"' in shown
        assert "/bfheroes/leaderboard" not in shown and "/bfheroes/host" not in shown
        assert 'name="next" value="/bfheroes/play"' in shown

        form = {"csrf": form_token(page.text), "next": "/bfheroes/play"}
        refused = await client.post(
            f"{base}/sign-in", data={**form, "email": "ann@example.test", "password": "wrong"}
        )
        # A refused sign-in continues on the sign-in page, which still returns to the game's page.
        assert refused.status_code == 400
        assert 'name="next" value="/bfheroes/play"' in refused.text
        signed_in = await client.post(
            f"{base}/sign-in", data={**form, "email": "ann@example.test", "password": PASSWORD}
        )
        assert signed_in.headers["location"] == "/bfheroes/play"

        bar = await client.get(f"{base}/pl/bfheroes/play")
        assert 'name="password"' not in bar.text
        assert "2500 Funds 0 Valor Points" in visible(bar.content)

    # The bar shows the allowance that a wallet opens with; only spending opens it.
    assert await select_value(database_url, "SELECT count(*) FROM wallet") == 0


async def test_the_games_own_addresses_open_its_pages_in_the_browsers_language(base: str) -> None:
    # The game opens its website's addresses in the system browser, which OpenFESLHook sends
    # here; a browser asking for them names no language, so it is not sent to one first.
    arriving = {
        "Accept-Language": "pl,en;q=0.5",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Dest": "document",
    }
    site = f"{base}/open/www.battlefieldheroes.com"
    async with httpx2.AsyncClient() as client:
        creator = await client.get(f"{site}/user/createHero", headers=arriving)
        funds = await client.get(f"{site}/store/purchaseFunds", headers=arriving)
        english = await client.get(f"{site}/user/createHero")
        unknown = await client.get(f"{base}/open/example.test/user/createHero", headers=arriving)
    assert creator.headers["location"] == "/pl/bfheroes/user/createHero?from=game"
    # An address whose page the site does not have yet opens the game's front page.
    assert funds.headers["location"] == "/pl/bfheroes"
    assert english.headers["location"] == "/bfheroes/user/createHero?from=game"
    assert unknown.status_code == 404
