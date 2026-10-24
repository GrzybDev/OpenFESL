import hashlib
import uuid
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import pytest
from support import fetch, game, log_in_player

from openfesl.core.texts import load_texts
from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
EASO = "easo.ea.com"
EDITORIAL = "/easo/editorial/BF/2010/BFBC2"
NO_STORE = "no-store, no-transform"


async def test_the_game_fetches_its_editorial_files_while_other_requests_reach_the_app(
    server_url: str,
) -> None:
    status, headers, body = await fetch(
        server_url, f"{EDITORIAL}/config/PC/game.xml", "EASO.ea.com:80"
    )
    assert (status, headers["Cache-Control"]) == (200, NO_STORE)
    config = ElementTree.fromstring(body)
    ticker = {lang.get("id"): lang.text for lang in config.iter("lang")}
    news = load_texts("news")
    assert ticker == {"en-US": news["en"], "pl-PL": news["pl"]}
    # The game unlocks the map revision only with the key it derives from the level itself.
    revision = config.find("revision")
    assert revision is not None
    digest = hashlib.md5(f"PC{revision.get('level')}".encode()).digest()
    assert revision.get("key") == str(uuid.UUID(bytes_le=digest)).upper()
    # The game compares this with its own build and offers an update when it differs.
    assert (await fetch(server_url, f"{EDITORIAL}/config/PC/version", EASO))[2] == b"795745"
    store = ElementTree.fromstring(
        (await fetch(server_url, f"{EDITORIAL}/store/PC/overlay.xml", EASO))[2]
    )
    assert (store.tag, len(store)) == ("offers", 0)

    for path in ("/health", f"{EDITORIAL}/config/PC/missing.xml"):
        status, headers, _ = await fetch(server_url, path, EASO)
        assert (status, headers["Cache-Control"]) == (404, NO_STORE)
    status, _, body = await fetch(server_url, "/health")
    assert (status, body) == (200, b"ok")


async def test_the_locker_lists_an_empty_locker_to_a_persona_that_is_playing(
    server_url: str,
) -> None:
    async with game(server_url) as player:
        await log_in_player(player, FESL, "ann@example.test")
        await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", "Ann_01")])
        persona = await player.request(
            FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "Ann_01")]
        )
        url = urlsplit((await player.request(FESL, "acct", [("TXN", "GetLockerURL")]))["url"])
        # The hook sends the game's HTTP here only for this host.
        assert url.hostname == EASO

        async def locker(command: str, key: str) -> tuple[int, bytes]:
            query = {"site": "easo", "cmd": command, "lkey": key, "pers": "Ann_01"}
            path = f"{url.path}?{urlencode(query | {'game': '/eagames/BFBC2'})}"
            status, headers, body = await fetch(server_url, path, EASO)
            assert headers["Cache-Control"] == NO_STORE
            return status, body

        status, body = await locker("dir", persona["lkey"])
        listing = ElementTree.fromstring(body).attrib
        assert status == 200
        assert (listing["error"], listing["ownr"], listing["pers"]) == (
            "0",
            persona["userId"],
            "Ann_01",
        )
        assert (listing["numFiles"], listing["maxFiles"], listing["maxBytes"]) == ("0", "0", "0")
        refused = ElementTree.fromstring((await locker("dir", "not-a-key."))[1])
        assert refused.attrib == {"error": "2"}
        assert (await locker("put", persona["lkey"]))[0] == 501
