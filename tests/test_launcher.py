from xml.etree import ElementTree

import httpx2
import pytest
from support import FESL, cli, fetch, game, log_in_player

from openfesl.core.games import Games, load_games
from openfesl.core.launcher import LauncherSession, LauncherSessions
from openfesl.server import app

pytestmark = pytest.mark.anyio

NUCLEUS = "accounts.example.test"


@pytest.fixture(autouse=True)
def nucleus_host(monkeypatch: pytest.MonkeyPatch) -> None:
    # No game that ships logs in through a launcher yet, so Bad Company 2 is given an invented
    # Nucleus host; the server starts with it.
    def load() -> Games:
        bfbc2 = load_games().by_id("bfbc2")
        assert bfbc2 is not None
        http = bfbc2.http.model_copy(update={"nucleus": NUCLEUS})
        return Games([bfbc2.model_copy(update={"http": http})])

    monkeypatch.setattr(app, "load_games", load)


async def sign_in(
    server_url: str, account: str, password: str, game: str = "bfbc2"
) -> httpx2.Response:
    base = server_url.replace("ws://", "http://", 1).rstrip("/")
    body = {"game": game, "role": "play", "account": account, "password": password}
    async with httpx2.AsyncClient() as client:
        return await client.post(f"{base}/launcher/session", json=body)


async def test_a_launcher_session_is_found_until_it_expires() -> None:
    now = [0.0]
    sessions = LauncherSessions(ttl=10.0, clock=lambda: now[0])
    session = LauncherSession(token="a-token", account_name="ann", persona_name="ann")

    session_id = sessions.issue(session)

    assert sessions.find(session_id) == session
    assert sessions.find("made-up") is None
    now[0] = 10.0
    assert sessions.find(session_id) is None


async def test_the_launcher_signs_a_player_in_and_the_game_logs_in_with_its_token(
    server_url: str,
) -> None:
    # The launcher signs the player in over its own route, the game started with the session id
    # fetches its Nucleus token over HTTP, and that token logs the account and the persona the
    # launcher named in over FESL.
    account = "ann@example.test"
    async with game(server_url) as registering:
        await log_in_player(registering, FESL, account)

    signed_in = await sign_in(server_url, account, "secret-pass")
    assert signed_in.status_code == 200
    session = signed_in.json()
    # An account without a persona is given one named after its login.
    assert (session["accountName"], session["personaName"]) == (account, "ann")

    cookie = {"Cookie": f"magma={session['sessionId']}"}
    code, _, answer = await fetch(server_url, "/nucleus/authToken", NUCLEUS, cookie)
    assert code == 200
    # A Magma result the game accepts has a <success> root; any other it reads as a denial.
    root = ElementTree.fromstring(answer)
    assert root.tag == "success"
    token = root.findtext("token")
    assert token

    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        silent = [("TXN", "NuLogin"), ("encryptedInfo", token), ("macAddr", "$0a0b0c0d0e0f")]
        logged_in = await player.request(FESL, "acct", silent)
        assert logged_in["nuid"] == account
        persona = [("TXN", "NuLoginPersona"), ("name", session["personaName"])]
        assert "errorCode" not in await player.request(FESL, "acct", persona)

    # A second sign-in keeps the persona the first one made.
    assert (await sign_in(server_url, account, "secret-pass")).json()["personaName"] == "ann"


async def test_a_sign_in_is_refused_with_a_reason_the_launcher_words(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    account = "ann@example.test"
    async with game(server_url) as player:
        await log_in_player(player, FESL, account)

    wrong = await sign_in(server_url, account, "a-wrong-password")
    unknown = await sign_in(server_url, account, "secret-pass", game="made-up")
    await cli(capsys, "accounts", "state", account, "banned")
    banned = await sign_in(server_url, account, "secret-pass")

    assert (wrong.status_code, wrong.json()) == (401, {"error": "wrong-sign-in"})
    assert (unknown.status_code, unknown.json()) == (404, {"error": "unknown-game"})
    assert (banned.status_code, banned.json()) == (401, {"error": "banned"})


async def test_an_unknown_session_fetches_no_token(server_url: str) -> None:
    code, _, answer = await fetch(server_url, "/nucleus/authToken?magma=made-up", NUCLEUS)

    assert code == 200
    assert ElementTree.fromstring(answer).tag == "error"


async def test_the_games_telemetry_is_accepted(server_url: str) -> None:
    # The game reads any reply but a <success> root, an empty one included, as a denial and logs it.
    answers = [
        await fetch(server_url, path, NUCLEUS) for path in ("/dc/submit", "/dc/submitTelemetry")
    ]

    for code, _, body in answers:
        assert code == 200
        assert ElementTree.fromstring(body).tag == "success"
