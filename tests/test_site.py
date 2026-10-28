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
