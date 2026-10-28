import re
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from heroes import MAGMA, create
from support import FESL, fetch, game, visible

from openfesl.core.start_links import LinkState, StartLinks
from openfesl.web import play_now

pytestmark = pytest.mark.anyio

PLAY_NOW = "/bfheroes/play-now"
LINK = re.compile(r'data-start-link="openfesl-bfheroes://launch\?role=play&amp;code=([^"]+)"')
STATUS = re.compile(r'data-status="([^"]+)"')


async def redeem(base: str, code: str, role: str = "play") -> httpx2.Response:
    """Trade a start link's code as OpenFESLHook's launcher does when a website link opens it."""
    async with httpx2.AsyncClient() as launcher:
        body = {"game": "bfheroes", "role": role, "code": code}
        return await launcher.post(f"{base}/launcher/session", json=body)


async def open_play_now(client: httpx2.AsyncClient, base: str) -> tuple[str, str]:
    """Open the Play Now page and return its start link's code and the address of its state."""
    page = await client.get(f"{base}{PLAY_NOW}")
    assert page.headers["cache-control"] == "no-store"
    code, status = LINK.search(page.text), STATUS.search(page.text)
    assert code and status, visible(page.content)
    return code[1], status[1].replace("&amp;", "&")


async def test_play_now_starts_the_default_hero_through_a_link_that_works_once(
    base: str, server_url: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{PLAY_NOW}")).content)
        assert "Sign in to play" in signed_out
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        no_hero = (await client.get(f"{base}{PLAY_NOW}")).text
        assert "Play Now needs a hero" in visible(no_hero.encode())
        assert "data-start-link" not in no_hero
        await create(client, base, "Mossgrove")
        # An account signed in through a service may have no game password; Play Now needs none.
        await execute(
            database_url, "UPDATE account SET password_hash = NULL WHERE nuid = 'ann@example.test'"
        )

        code, status = await open_play_now(client, base)
        waiting = visible((await client.get(f"{base}{status}")).content)
        assert "Starting Battlefield Heroes as Mossgrove" in waiting
        # The launcher refuses a website link to host, which leaves the link working.
        hosting = await redeem(base, code, role="host")
        session = await redeem(base, code)
        again = await redeem(base, code)
        taken = visible((await client.get(f"{base}{status}")).content)

        # A page opened again makes a new link and ends the earlier one.
        earlier, earlier_status = await open_play_now(client, base)
        _, newer_status = await open_play_now(client, base)
        replaced = await redeem(base, earlier)
        ended = visible((await client.get(f"{base}{earlier_status}")).content)
        # A link nobody took for a while says what to do when nothing starts.
        monkeypatch.setattr(play_now, "PATIENCE", timedelta(0))
        missing = visible((await client.get(f"{base}{newer_status}")).content)
        # Signing out everywhere ends the links that the ended sessions made.
        newest, _ = await open_play_now(client, base)
        await execute(
            database_url,
            "UPDATE account SET session_epoch = session_epoch + 1 WHERE nuid = 'ann@example.test'",
        )
        signed_out_everywhere = await redeem(base, newest)

    assert (hosting.status_code, hosting.json()) == (401, {"error": "wrong-sign-in"})
    assert session.status_code == 200
    assert session.json()["personaName"] == "Mossgrove"
    assert (again.status_code, again.json()) == (401, {"error": "wrong-sign-in"})
    assert "Battlefield Heroes is starting" in taken
    assert "and logs in as Mossgrove" in taken
    assert replaced.json() == {"error": "wrong-sign-in"}
    assert "This start link has ended" in ended
    assert "The launcher has not taken the start link" in missing
    assert "Open Play links from the website with this copy" in missing
    assert signed_out_everywhere.json() == {"error": "wrong-sign-in"}

    # The session logs the game in as the hero, as a session from the account's password does.
    cookie = {"Cookie": f"magma={session.json()['sessionId']}"}
    _, _, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
    token = ElementTree.fromstring(body).findtext("token") or ""
    async with game(server_url) as player:
        await player.hello(FESL, "bfwest-pc", client_type="client-noreg")
        login = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
        logged_in = await player.request(FESL, "acct", login)
        hero = [("TXN", "NuLoginPersona"), ("name", "Mossgrove")]
        playing = await player.request(FESL, "acct", hero)
    assert "errorCode" not in logged_in and "errorCode" not in playing


async def test_a_start_link_ends_after_its_lifetime_unused() -> None:
    now = [datetime(2026, 10, 28, 18, 40, tzinfo=UTC)]
    links = StartLinks(timedelta(minutes=2), clock=lambda: now[0])
    link = links.make("bfheroes", 1, 0, "Mossgrove")
    other_game = links.make("bfbc2", 1, 0, "Mossgrove")
    now[0] += timedelta(minutes=2)

    assert links.take(link.code, "bfheroes") is None
    assert link.state(now[0]) is LinkState.ENDED
    # A code sent for another game is used up all the same, so it gets no second try.
    now[0] -= timedelta(minutes=1)
    assert links.take(other_game.code, "bfheroes") is None
    assert links.take(other_game.code, "bfbc2") is None
