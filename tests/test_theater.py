import time
from collections.abc import Iterator

import pytest
from support import enter_theater, game, log_in_player, log_in_server

from openfesl.core import theater
from openfesl.protocol.relay import Channel, ConnectionKey, Data

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)
DATAGRAMS = ConnectionKey(Channel.THEATER_DATAGRAM, 7)


@pytest.fixture
def behind_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BEHIND_TRUSTED_PROXY", "true")
    monkeypatch.setenv("CLIENT_IP_HEADER", "CF-Connecting-IP")
    yield


async def test_a_dedicated_server_logs_in_to_theater_and_learns_its_public_address(
    server_url: str,
) -> None:
    async with game(server_url) as server, game(server_url) as player:
        lkey = await log_in_server(server, FESL)
        await server.open(THEATER, "bfbc2-pc-server.theater.ea.com", port=18326)
        connected = await server.theater(THEATER, "CONN", [("PROT", "2"), ("PLAT", "PC")])
        assert connected.error is None
        assert (connected.fields["activityTimeoutSecs"], connected.fields["PROT"]) == ("240", "2")

        await log_in_player(player, FESL, "ann@example.test")
        await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", "Ann_01")])
        other = await player.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "Ann_01")])
        assert (await server.theater(THEATER, "USER", [("LKEY", other["lkey"])])).error == "auth"
        user = await server.theater(THEATER, "USER", [("LKEY", lkey), ("MAC", "$0a0b0c0d0e0f")])
        assert (user.error, user.fields["NAME"]) == (None, "bfbc2.server.p")
        unknown = await server.theater(THEATER, "ZZZZ")
        assert unknown.error == "misc"

        await server.open(DATAGRAMS, "bfbc2-pc-server.theater.ea.com", port=18326)
        echo = [("TID", "1"), ("TYPE", "1"), ("UID", "5")]
        await server.send_theater(DATAGRAMS, "ECHO", echo)
        answer = await server.theater_push(DATAGRAMS, "ECHO")
        assert answer.error is None
        assert answer.fields == {
            "IP": "127.0.0.1",
            "PORT": "50007",
            "TYPE": "1",
            "UID": "5",
            "TID": "1",
        }


async def test_theater_keeps_a_connection_that_answers_pings_and_closes_one_that_stops(
    server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(theater, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(theater, "ACTIVITY_TIMEOUT", 1)
    async with game(server_url) as server:
        await enter_theater(server, THEATER, "bfbc2-pc-server", await log_in_server(server, FESL))

        # Nothing but ping replies for longer than the activity timeout, as from an idle game.
        answering_until = time.monotonic() + 1.5
        while time.monotonic() < answering_until:
            frame = await server.next_frame()
            assert isinstance(frame, Data), "a connection that answers its pings was closed"
            if frame.connection == THEATER:
                assert (frame.packet.command, frame.packet.kind, frame.packet.payload) == (
                    "PING",
                    0,
                    b"\0",
                )
                await server.send_theater(THEATER, "PING", [("TID", "0")])
        assert (await server.theater(THEATER, "CONN", [("PROT", "2")])).error is None

        while True:
            frame = await server.next_frame()
            if not isinstance(frame, Data):
                break
        assert frame.connection == THEATER


async def test_a_session_without_a_public_ipv4_address_gets_no_echo(
    behind_proxy: None, server_url: str
) -> None:
    async with game(server_url, {"CF-Connecting-IP": "2001:db8::7"}) as server:
        await enter_theater(server, THEATER, "bfbc2-pc-server", await log_in_server(server, FESL))
        await server.open(DATAGRAMS, "bfbc2-pc-server.theater.ea.com", port=18326)
        await server.send_theater(DATAGRAMS, "ECHO", [("TID", "1"), ("TYPE", "1")])

        assert (await server.theater(THEATER, "CONN", [("PROT", "2")])).error is None
        assert not [
            frame
            for frame in server.frames
            if isinstance(frame, Data) and frame.connection == DATAGRAMS
        ]
