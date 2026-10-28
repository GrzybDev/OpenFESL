from xml.etree import ElementTree

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support import FESL, fetch, game, launcher_sign_in, log_in_player

pytestmark = pytest.mark.anyio

MAGMA = "www.battlefieldheroes.com"
NO_STORE = "no-store, no-transform"


async def play(server_url: str, account: str) -> tuple[dict[str, str], int]:
    """Register an account in another game, then start Battlefield Heroes with it as the hook does.

    Returns:
        The magma cookie the game sends and the number of the hero it plays.
    """
    async with game(server_url) as registering:
        await log_in_player(registering, FESL, account)
    session = (await launcher_sign_in(server_url, account, "secret-pass")).json()
    cookie = {"Cookie": f"magma={session['sessionId']}"}
    _, _, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
    token = ElementTree.fromstring(body).findtext("token") or ""
    async with game(server_url) as player:
        await player.hello(FESL, "bfwest-pc", client_type="client-noreg")
        silent = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
        await player.request(FESL, "acct", silent)
        hero = [("TXN", "NuLoginPersona"), ("name", session["personaName"])]
        playing = await player.request(FESL, "acct", hero)
    return cookie, int(playing["userId"])


def refusal(body: bytes) -> tuple[str | None, str | None, str | None]:
    """Return the code, cause and field of a Magma error document."""
    answer = ElementTree.fromstring(body)
    failure = answer.find("failure")
    assert answer.tag == "error" and failure is not None
    return answer.get("code"), failure.get("cause"), failure.get("field")


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


async def test_a_heros_items_load_by_its_number_beside_the_stores_empty_lists(
    server_url: str, database_url: str
) -> None:
    cookie, hero = await play(server_url, "hero@example.test")
    # A consumable with uses left and a rental, as the database keeps a hero's items.
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO entitlement (account_id, persona_id, tag, use_count, expires_at,"
                " status, granted_at) SELECT account_id, id, item.*, 'ACTIVE', '2026-10-20 12:00Z'"
                " FROM persona, (VALUES ('2005-lmct', 3, NULL::timestamptz),"
                " ('3002-tlmd', NULL, '2026-11-03 18:30+01')) AS item WHERE persona.id = :hero"
            ),
            {"hero": hero},
        )
    await engine.dispose()

    status, headers, body = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
    items = [
        [entry.findtext(name) for name in ("entitlementTag", "useCount", "terminationDate")]
        for entry in ElementTree.fromstring(body).iter("entitlement")
    ]
    # The store's refresh after a purchase asks for the item a tag names, whichever way the hero
    # owns it.
    refreshed = f"/nucleus/entitlements/{hero}?entitlementTag=3002-unlm"
    _, _, body = await fetch(server_url, refreshed, MAGMA, cookie)
    found = [entry.findtext("entitlementTag") for entry in ElementTree.fromstring(body)]
    store = [
        await fetch(server_url, path, MAGMA, cookie)
        for path in ("/ofb/products", "/en/game/hotDeals")
    ]

    assert (status, headers["Cache-Control"]) == (200, NO_STORE)
    assert items == [["2005-lmct", "3", ""], ["3002-tlmd", "0", "2026-11-03T17:30Z"]]
    assert found == ["3002-tlmd"]
    for status, _, body in store:
        assert (status, body) == (200, b"<products />")


async def test_a_hero_of_another_account_is_refused_with_the_errors_the_game_maps(
    server_url: str,
) -> None:
    cookie, _ = await play(server_url, "ann@example.test")
    _, other = await play(server_url, "bob@example.test")

    refusals = [
        refusal((await fetch(server_url, path, MAGMA, cookie))[2])
        for path in (f"/nucleus/entitlements/{other}", "/nucleus/entitlements/bob")
    ]

    # The game reports them as errors 6011 and 6009.
    assert refusals == [
        ("VALIDATION_FAILED", "USER_DOES_NOT_MATCH", None),
        ("VALIDATION_FAILED", "NOT_NUMERIC", "userId"),
    ]
