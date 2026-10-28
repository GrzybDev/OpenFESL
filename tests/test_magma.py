from xml.etree import ElementTree

import pytest
from support import FESL, fetch, game, launcher_sign_in, log_in_player

pytestmark = pytest.mark.anyio

MAGMA = "www.battlefieldheroes.com"
NO_STORE = "no-store, no-transform"


async def test_a_game_started_by_the_launcher_logs_in_with_the_token_of_its_session(
    server_url: str,
) -> None:
    # Battlefield Heroes registers no accounts, so the account comes from another game here.
    account = "hero@example.test"
    async with game(server_url) as registering:
        await log_in_player(registering, FESL, account)
    signed_in = await launcher_sign_in(server_url, account, "secret-pass")
    assert signed_in.status_code == 200
    session = signed_in.json()
    # An account without a hero in the game is given one named after its login.
    assert (session["accountName"], session["personaName"]) == (account, "hero")

    # The game started with the session id sends it as its magma cookie for the token.
    cookie = {"Cookie": f"magma={session['sessionId']}"}
    status, headers, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
    assert (status, headers["Cache-Control"]) == (200, NO_STORE)
    answer = ElementTree.fromstring(body)
    token = answer.findtext("token")
    assert answer.tag == "success" and token

    async with game(server_url) as player:
        await player.hello(FESL, "bfwest-pc", client_type="client-noreg", locale="pl_PL")
        silent = [
            ("TXN", "NuLogin"),
            ("returnEncryptedInfo", "0"),
            ("encryptedInfo", token),
            ("macAddr", "$0a0b0c0d0e0f"),
        ]
        logged_in = await player.request(FESL, "acct", silent)
        personas = await player.request(FESL, "acct", [("TXN", "NuGetPersonas"), ("namespace", "")])
        details = await player.request(FESL, "acct", [("TXN", "NuGetAccount")])
        playing = await player.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "hero")])
        # While it plays, the game posts its data collection with the same cookie.
        dataset = b'<dataset><insert table="hwprofile" ref="1">\n</insert>\n</dataset>'
        collected = [
            await fetch(server_url, path, MAGMA, cookie, dataset)
            for path in ("/dc/submit", "/dc/submitTelemetry")
        ]

    assert (logged_in["nuid"], logged_in["userId"]) == (account, details["userId"])
    assert (personas["personas.[]"], personas["personas.0"]) == ("1", "hero")
    # The game keeps the account's country and language; the language is the game's own.
    assert (details["country"], details["language"]) == ("PL", "pl")
    assert "errorCode" not in playing
    for status, _, body in collected:
        assert (status, ElementTree.fromstring(body).tag) == (200, "success")
    # A second sign-in keeps the hero the first one made.
    again = await launcher_sign_in(server_url, account, "secret-pass")
    assert again.json()["personaName"] == "hero"


async def test_a_token_request_without_a_known_session_gets_an_error_the_game_logs(
    server_url: str,
) -> None:
    # A game started without a session sends magma=0; an expired or made-up session is unknown.
    refusals = {}
    for session_id in ("0", "made-up"):
        cookie = {"Cookie": f"magma={session_id}"}
        status, _, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
        answer = ElementTree.fromstring(body)
        failure = answer.find("failure")
        assert failure is not None
        refusals[session_id] = (status, answer.tag, answer.get("code"), failure.get("cause"))

    assert refusals == {
        "0": (200, "error", "NOT_LOGGED_IN", "NO_SESSION"),
        "made-up": (200, "error", "NOT_LOGGED_IN", "UNKNOWN_SESSION"),
    }
