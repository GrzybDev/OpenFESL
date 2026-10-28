from collections.abc import AsyncIterator

import httpx2
import pytest
from identity import form_token
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support import FESL, Game, cli, game, markup, registration

from openfesl.core.limits import FAILURES, Attempts

pytestmark = pytest.mark.anyio

PASSWORD = "secret-pass"


async def register(player: Game, address: str) -> None:
    terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
    answer = await player.request(FESL, "acct", registration(address, PASSWORD, terms))
    assert "errorCode" not in answer


async def post_sign_in(
    client: httpx2.AsyncClient, base: str, address: str, password: str, **fields: str
) -> httpx2.Response:
    page = await client.get(f"{base}/sign-in")
    form = {"csrf": form_token(page.text), "email": address, "password": password, **fields}
    return await client.post(f"{base}/sign-in", data=form)


async def sign_out(client: httpx2.AsyncClient, base: str) -> httpx2.Response:
    page = await client.get(f"{base}/terms")
    return await client.post(f"{base}/sign-out", data={"csrf": form_token(page.text)})


@pytest.fixture
async def base(server_url: str) -> AsyncIterator[str]:
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        for address in ("ann@example.test", "bob@example.test"):
            await register(player, address)
    yield server_url.replace("ws://", "http://").rstrip("/")


async def test_an_account_made_in_a_game_signs_in_with_its_address_and_game_password(
    base: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}/sign-in")
        shown = markup(page.content)
        assert 'href="/sign-in" aria-current="page"' in shown
        # Without a mail server and with the site's confirmation on, nobody can register.
        assert "Registration needs the server&#39;s mail, which is not set up." in shown
        assert "Forgotten game password" not in shown
        assert "Each game keeps its own: soldiers in Bad Company 2 and heroes in" in shown

        # An unknown address and a wrong password get one answer, so neither reveals an account.
        for address, password in (
            ("ann@example.test", "wrong-pass"),
            ("eve@example.test", PASSWORD),
        ):
            refused = await post_sign_in(client, base, address, password)
            assert refused.status_code == 400
            assert "do not match an account." in refused.text
            assert f'value="{address}"' in refused.text

        signed_in = await post_sign_in(client, base, "ANN@example.test", PASSWORD)
        assert (signed_in.status_code, signed_in.headers["location"]) == (303, "/account")
        menu = markup((await client.get(f"{base}/pl")).content)
        assert "Brak nazwy wyświetlanej" in menu and "adresem e-mail" in menu
        assert 'href="/pl/account/display-name"' in menu and 'href="/pl/account"' not in menu
        assert (await client.get(f"{base}/sign-in")).headers["location"] == "/account"

        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE account SET display_name = 'Invented_Ann' WHERE nuid = :nuid"),
                {"nuid": "ann@example.test"},
            )
        await engine.dispose()
        menu = markup((await client.get(f"{base}/terms")).content)
        assert "Invented_Ann" in menu and "with the e-mail address" in menu
        assert "ann@example.test" not in menu

        # A new state ends the web session at the next page, as a new password does.
        await cli(capsys, "accounts", "state", "ann@example.test", "banned")
        assert "Invented_Ann" not in (await client.get(f"{base}/terms")).text
        # A ban keeps the account from games, not from its own pages.
        assert (await post_sign_in(client, base, "ann@example.test", PASSWORD)).status_code == 303
        assert (await sign_out(client, base)).headers["location"] == "/sign-in"
        assert "This site&#39;s session has ended." in (await client.get(f"{base}/sign-in")).text
        assert "Invented_Ann" not in (await client.get(f"{base}/terms")).text

        # Only the site's own pages open after signing in.
        for asked, opened in (("/pl/terms", "/pl/terms"), ("//elsewhere.test/", "/account")):
            done = await post_sign_in(client, base, "bob@example.test", PASSWORD, next=asked)
            assert done.headers["location"] == opened
            await sign_out(client, base)

        # A form from another session signs nobody in.
        stale = await client.post(
            f"{base}/sign-in",
            data={"csrf": "invented", "email": "bob@example.test", "password": PASSWORD},
        )
        assert stale.status_code == 303
        assert "The form expired. Try again." in (await client.get(f"{base}/sign-in")).text


async def test_waiting_and_deactivated_accounts_are_named_only_after_the_right_password(
    server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    await cli(capsys, "settings", "set", "bfbc2.accounts.email_verification", "on")
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        await register(player, "kim@example.test")
        await register(player, "dan@example.test")
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        wrong = await post_sign_in(client, base, "kim@example.test", "wrong-pass")
        assert "not confirmed" not in wrong.text
        waiting = await post_sign_in(client, base, "kim@example.test", PASSWORD)
        assert waiting.status_code == 403
        assert "The link sent to kim@example.test on " in waiting.text

        # The policy the account was registered under decides, as games' policies do for games.
        await cli(capsys, "settings", "reset", "bfbc2.accounts.email_verification")
        assert (await post_sign_in(client, base, "kim@example.test", PASSWORD)).status_code == 303
        engine = create_async_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE account SET made_in = NULL WHERE nuid = 'kim@example.test'")
            )
        await engine.dispose()
        await sign_out(client, base)
        refused = await post_sign_in(client, base, "kim@example.test", PASSWORD)
        assert refused.status_code == 403
        assert (
            await cli(capsys, "settings", "set", "site.accounts.email_verification", "off")
            == "off\n"
        )
        assert (await post_sign_in(client, base, "kim@example.test", PASSWORD)).status_code == 303
        await sign_out(client, base)

        await cli(capsys, "accounts", "state", "dan@example.test", "deactivated")
        assert "deactivated" not in (await post_sign_in(client, base, "dan@example.test", "x")).text
        deactivated = await post_sign_in(client, base, "dan@example.test", PASSWORD)
        assert deactivated.status_code == 403
        assert "A deletion request deactivated it on " in deactivated.text


async def test_sign_in_pauses_per_address_whether_or_not_an_account_uses_it(base: str) -> None:
    async with httpx2.AsyncClient() as client:
        for address in ("ann@example.test", "nobody@example.test"):
            for _ in range(FAILURES):
                assert (await post_sign_in(client, base, address, "wrong-pass")).status_code == 400
            paused = await post_sign_in(client, base, address.upper(), PASSWORD)
            assert paused.status_code == 429
            assert "Sign-in with this address is paused for a few minutes." in paused.text
        assert (await post_sign_in(client, base, "bob@example.test", PASSWORD)).status_code == 303


def test_a_paused_address_resumes_once_its_oldest_failure_is_old_enough() -> None:
    now = [0.0]
    attempts = Attempts(limit=2, window=10.0, clock=lambda: now[0])
    attempts.failed("ann@example.test")
    now[0] = 6.0
    attempts.failed("ANN@example.test ")
    paused = attempts.paused("ann@example.test")
    now[0] = 11.0
    resumed = attempts.paused("ann@example.test")
    attempts.failed("ann@example.test")
    again = attempts.paused("ann@example.test")
    attempts.clear("ann@example.test")
    assert (paused, resumed, again, attempts.paused("ann@example.test")) == (
        True,
        False,
        True,
        False,
    )
