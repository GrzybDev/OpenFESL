import asyncio
from collections.abc import AsyncIterator

import httpx2
import pytest
import uvicorn
from accounts import PASSWORD, confirm_address, execute, post_sign_in, register, sign_out
from identity import form_token
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from support import FESL, Mailbox, cli, game, links, markup

from openfesl.core.limits import FAILURES, Attempts
from openfesl.web import turnstile as turnstile_module

pytestmark = pytest.mark.anyio


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
        reset = await client.get(f"{base}/password-reset")
        assert "A game password reset needs the server&#39;s mail" in reset.text
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
        # An account made in a game chooses its display name before any other page.
        assert (signed_in.status_code, signed_in.headers["location"]) == (
            303,
            "/account/display-name",
        )
        menu = markup((await client.get(f"{base}/pl")).content)
        assert "Brak nazwy wyświetlanej" in menu and "adresem e-mail" in menu
        assert 'href="/pl/account/display-name"' in menu and 'href="/pl/account"' not in menu
        assert (await client.get(f"{base}/sign-in")).headers["location"] == "/account"

        await execute(
            database_url,
            "UPDATE account SET display_name = 'Invented_' || initcap(split_part(nuid, '@', 1))"
            " WHERE nuid IN ('ann@example.test', 'bob@example.test')",
        )
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
        await execute(
            database_url, "UPDATE account SET made_in = NULL WHERE nuid = 'kim@example.test'"
        )
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


@pytest.fixture
async def turnstile(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[list[dict[str, str]]]:
    # Cloudflare's siteverify, which passes the token "passed"; set before the server starts.
    checked: list[dict[str, str]] = []

    async def siteverify(request: Request) -> Response:
        form = {key: str(value) for key, value in (await request.form()).items()}
        checked.append(form)
        return JSONResponse({"success": form.get("response") == "passed"})

    app = Starlette(routes=[Route("/siteverify", siteverify, methods=["POST"])])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    monkeypatch.setattr(turnstile_module, "SITEVERIFY", f"http://127.0.0.1:{port}/siteverify")
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "invented-site-key")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "invented-secret-key")
    yield checked
    server.should_exit = True
    await task


async def test_the_cloudflare_check_guards_the_password_form_before_anything_is_counted(
    turnstile: list[dict[str, str]], base: str
) -> None:
    async with httpx2.AsyncClient() as client:
        # A game's own site shows no check on its pages, so they lead to the sign-in page.
        strip = await client.get(f"{base}/bfheroes/play")
        assert 'name="password"' not in strip.text and "openfesl_session" not in strip.cookies
        assert ("/sign-in?next=%2Fbfheroes%2Fplay", "Sign in") in links(markup(strip.content))
        # The creator that the game opens points to no sign-in fields above it then.
        arrival = await client.get(f"{base}/bfheroes/user/createHero", params={"from": "game"})
        assert "Heroes belong to that account" in arrival.text
        assert "sign-in above" not in arrival.text
        page = await client.get(f"{base}/sign-in", params={"next": "/bfheroes/play"})
        assert 'data-sitekey="invented-site-key"' in page.text
        assert "Cloudflare Turnstile to tell people from automated requests" in page.text
        policy = page.headers["content-security-policy"]
        assert "script-src 'self' https://challenges.cloudflare.com;" in policy
        assert "frame-src https://challenges.cloudflare.com" in policy
        assert (
            "challenges.cloudflare.com"
            not in (await client.get(f"{base}/terms")).headers["content-security-policy"]
        )

        for token in ["", *["failed"] * FAILURES]:
            refused = await post_sign_in(
                client, base, "ann@example.test", "wrong-pass", **{"cf-turnstile-response": token}
            )
            assert refused.status_code == 400
            assert "The check did not pass. Try again." in refused.text
        passed = await post_sign_in(
            client,
            base,
            "ann@example.test",
            PASSWORD,
            next="/bfheroes/play",
            **{"cf-turnstile-response": "passed"},
        )
        # The account made in a game names itself first, then returns to the game's page.
        assert passed.headers["location"] == "/account/display-name?next=%2Fbfheroes%2Fplay"
    # Cloudflare compares the token with the address of the client that passed the check.
    assert turnstile[-1] == {
        "secret": "invented-secret-key",
        "response": "passed",
        "remoteip": "127.0.0.1",
    }


async def test_a_waiting_account_gets_a_new_link_at_most_once_per_interval(
    mailbox: Mailbox, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    await cli(capsys, "settings", "set", "bfbc2.accounts.email_verification", "on")
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        await register(player, "kim@example.test")
    _, first = await mailbox.receive("kim@example.test")
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        # Only an account that proved to be the visitor's asks for a link.
        page = await client.get(f"{base}/sign-in")
        asked = await client.post(
            f"{base}/sign-in/confirmation", data={"csrf": form_token(page.text)}
        )
        assert asked.status_code == 303

        waiting = await post_sign_in(client, base, "kim@example.test", PASSWORD)
        assert 'action="/sign-in/confirmation"' in waiting.text
        # Requests sent together send one mail.
        sent, again = sorted(
            await asyncio.gather(
                *(
                    client.post(
                        f"{base}/sign-in/confirmation", data={"csrf": form_token(waiting.text)}
                    )
                    for _ in range(2)
                )
            ),
            key=lambda answer: answer.status_code,
        )
        assert "A new link was sent to kim@example.test." in sent.text
        _, second = await mailbox.receive("kim@example.test")
        assert again.status_code == 429
        assert "Another one can be sent from " in again.text

        # The new link replaces the one sent on registration.
        assert (await client.get(f"{base}{first}")).status_code == 404
        assert (await confirm_address(client, base, second)).status_code == 200
        assert (await post_sign_in(client, base, "kim@example.test", PASSWORD)).status_code == 303
    assert not mailbox.messages
