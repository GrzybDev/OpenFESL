import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from identity import form_token
from support import markup, visible

from openfesl.core.history import OnlineHistory

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
        assert 'href="/bfheroes/leaderboards"' in shown
        assert 'href="/bfheroes/leaderboard"' not in shown and "/bfheroes/host" not in shown
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
        older = await client.get(f"{site}/store/purchaseBattlefunds", headers=arriving)
        redeem = await client.get(f"{site}/store/redeem", headers=arriving)
        english = await client.get(f"{site}/user/createHero")
        unknown = await client.get(f"{base}/open/example.test/user/createHero", headers=arriving)
    assert creator.headers["location"] == "/pl/bfheroes/user/createHero?from=game"
    # 1.46 names the Funds page's address by the currency's older name.
    assert funds.headers["location"] == older.headers["location"]
    assert funds.headers["location"] == "/pl/bfheroes/store/purchaseFunds?from=game"
    # An address whose page the site does not have opens the game's front page.
    assert redeem.headers["location"] == "/pl/bfheroes"
    assert english.headers["location"] == "/bfheroes/user/createHero?from=game"
    assert unknown.status_code == 404


async def test_a_game_sites_missing_and_failing_pages_keep_its_frame(
    base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise RuntimeError("the stats are unreadable")

    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        missing = await client.get(f"{base}/pl/bfheroes/invented-page")
        monkeypatch.setattr(OnlineHistory, "overview", fail)
        failing = await client.get(f"{base}/bfheroes?again=1")
        elsewhere = await client.get(f"{base}/invented-page")

    assert missing.status_code == 404
    # The site's header, menus and bar stay around the page.
    assert "Battlefield Heroes w OpenFESL" in visible(missing.content)
    assert "2500 Funds" in visible(missing.content)
    assert "Nie znaleziono Ta strona nie istnieje w tym serwisie." in visible(missing.content)
    assert failing.status_code == 500
    assert "unreadable" not in failing.text
    assert "Server error The server could not show this page." in visible(failing.content)
    assert 'href="/bfheroes?again=1"' in failing.text
    # Addresses outside the game's site keep OpenFESL's own page.
    assert elsewhere.status_code == 404 and "Page not found" in visible(elsewhere.content)
