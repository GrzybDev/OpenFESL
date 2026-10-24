import time
from collections.abc import Iterator

import anyio
import pytest
from support import Game, enter_theater, game, log_in_persona, log_in_server

from openfesl.core import theater
from openfesl.protocol.relay import Channel, ConnectionKey, Data

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)
DATAGRAMS = ConnectionKey(Channel.THEATER_DATAGRAM, 7)

# What the R34 dedicated server sends: no lobby, no attributes, empty UGID and SECRET.
CREATE_GAME = [
    ("LID", "-1"),
    ("RESERVE-HOST", "0"),
    ("NAME", "Invented Server #1"),
    ("PORT", "19567"),
    ("HTTYPE", "A"),
    ("TYPE", "G"),
    ("QLEN", "0"),
    ("DISABLE-AUTO-DEQUEUE", "0"),
    ("HXFR", "0"),
    ("INT-PORT", "19567"),
    ("INT-IP", "192.168.1.10"),
    ("MAX-PLAYERS", "32"),
    ("B-maxObservers", "0"),
    ("B-numObservers", "0"),
    ("UGID", ""),
    ("SECRET", ""),
    ("B-version", "ROMEPC795745"),
    ("JOIN", "O"),
]


@pytest.fixture
def behind_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BEHIND_TRUSTED_PROXY", "true")
    monkeypatch.setenv("CLIENT_IP_HEADER", "CF-Connecting-IP")
    yield


async def games_in_lobby(game: Game, key: ConnectionKey) -> int:
    lobbies = await game.theater(key, "LLST", [("FILTER-FAV-ONLY", "0"), ("FAV-PLAYER", "")])
    assert lobbies.fields["NUM-LOBBIES"] == "1"
    row = await game.theater_push(key, "LDAT")
    assert (row.fields["TID"], row.fields["LID"], row.fields["NAME"]) == (
        lobbies.fields["TID"],
        "1",
        "bfbc2PC01",
    )
    return int(row.fields["NUM-GAMES"])


async def test_a_dedicated_server_logs_in_to_theater_and_learns_its_public_address(
    server_url: str,
) -> None:
    async with game(server_url) as server, game(server_url) as player:
        lkey = await log_in_server(server, FESL)
        await server.open(THEATER, "bfbc2-pc-server.theater.ea.com", port=18326)
        connected = await server.theater(THEATER, "CONN", [("PROT", "2"), ("PLAT", "PC")])
        assert connected.error is None
        assert (connected.fields["activityTimeoutSecs"], connected.fields["PROT"]) == ("240", "2")

        early = await server.theater(THEATER, "LLST", [("FILTER-FAV-ONLY", "0")])
        assert early.error == "auth"
        _, other = await log_in_persona(player, FESL, "ann@example.test", "Ann_01")
        assert (await server.theater(THEATER, "USER", [("LKEY", other)])).error == "auth"
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


async def test_a_dedicated_server_lists_updates_and_removes_its_game(server_url: str) -> None:
    async with game(server_url) as server, game(server_url) as player:
        await enter_theater(server, THEATER, "bfbc2-pc-server", await log_in_server(server, FESL))
        _, lkey = await log_in_persona(player, FESL, "ann@example.test", "Ann_01")
        await enter_theater(player, THEATER, "bfbc2-pc", lkey)
        assert await games_in_lobby(player, THEATER) == 0

        created = await server.theater(THEATER, "CGAM", CREATE_GAME)
        assert created.error is None
        fields = created.fields
        assert (fields["LID"], fields["MAX-PLAYERS"], fields["JOIN"]) == ("1", "32", "O")
        assert (len(fields["UGID"]), len(fields["EKEY"])) == (36, 16)
        assert fields["SECRET"]
        assert (await server.theater(THEATER, "CGAM", CREATE_GAME)).error == "ingm"
        assert (await server.theater(THEATER, "RGAM", [("TYPE", "G")])).error is None
        again = await server.theater(THEATER, "CGAM", CREATE_GAME)
        assert again.error is None
        assert again.fields["GID"] != fields["GID"]
        assert await games_in_lobby(player, THEATER) == 1

        game_id = [("LID", "1"), ("GID", again.fields["GID"])]
        opened = await server.theater(THEATER, "UBRA", [*game_id, ("START", "1")])
        bracket = [("TID", opened.fields["TID"])]
        update = [*game_id, ("NAME", "Invented Server #2"), ("B-U-level", "levels/mp_001")]
        await server.send_theater(THEATER, "UGAM", [*update, ("JOIN", "O"), *bracket])
        await server.send_theater(THEATER, "UGDE", [*game_id, ("D-pdat00", "|0|0|0|"), *bracket])
        closed = await server.theater(THEATER, "UBRA", [*game_id, ("START", "0")])
        assert (opened.error, closed.error) == (None, None)
        entered = await server.theater(THEATER, "PENT", [*game_id, ("PID", "4")])
        assert entered.error == "ntfn"
        assert (await server.theater(THEATER, "PLVT", [*game_id, ("PID", "4")])).error is None
        assert not [
            frame
            for frame in server.frames
            if isinstance(frame, Data) and frame.packet.command in {"UGAM", "UGDE"}
        ]

        await server.close(THEATER)
        with anyio.fail_after(5):
            while await games_in_lobby(player, THEATER):
                await anyio.sleep(0.01)


async def test_a_session_without_a_public_ipv4_address_can_neither_echo_nor_host(
    behind_proxy: None, server_url: str
) -> None:
    async with game(server_url, {"CF-Connecting-IP": "2001:db8::7"}) as server:
        await enter_theater(server, THEATER, "bfbc2-pc-server", await log_in_server(server, FESL))
        await server.open(DATAGRAMS, "bfbc2-pc-server.theater.ea.com", port=18326)
        await server.send_theater(DATAGRAMS, "ECHO", [("TID", "1"), ("TYPE", "1")])

        assert (await server.theater(THEATER, "CGAM", CREATE_GAME)).error == "misc"
        assert not [
            frame
            for frame in server.frames
            if isinstance(frame, Data) and frame.connection == DATAGRAMS
        ]
