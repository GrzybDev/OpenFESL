import struct
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

from openfesl.core.fesl import FeslConnection, Route
from openfesl.protocol.fields import format_fields, parse_fields
from openfesl.protocol.fsys import Hello
from openfesl.protocol.message import Message
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import (
    SUBPROTOCOL,
    Channel,
    ConnectionKey,
    Data,
    decode_frame,
    encode_frame,
)
from openfesl.server.cli import RelayServer

if TYPE_CHECKING:
    from openfesl.core.session import GameSession

pytestmark = pytest.mark.anyio

SERVER_FESL = ConnectionKey(Channel.FESL, 1)
OTHER_FESL = ConnectionKey(Channel.FESL, 2)

# The dedicated server's Hello in the key order its SDK writes.
SERVER_HELLO = [
    ("TXN", "Hello"),
    ("clientString", "bfbc2-pc-server"),
    ("sku", "125170"),
    ("locale", "en_US"),
    ("clientPlatform", "PC"),
    ("clientVersion", "2.0"),
    ("SDKVersion", "5.1.2.0.0"),
    ("protocolVersion", "2.0"),
    ("fragmentSize", "8096"),
    ("clientType", "server"),
]


def open_frame(key: ConnectionKey, host: str, port: int, game_port: int) -> bytes:
    return struct.pack(">BBHHHB", 1, key.channel, key.number, port, game_port, len(host)) + (
        host.encode("ascii")
    )


async def send(websocket: ClientConnection, key: ConnectionKey, packet: Packet) -> None:
    await websocket.send(encode_frame(Data(key, packet)))


async def receive(websocket: ClientConnection) -> tuple[ConnectionKey, Packet, dict[str, str]]:
    frame = decode_frame(await websocket.recv(decode=False))
    assert isinstance(frame, Data)
    return frame.connection, frame.packet, dict(parse_fields(frame.packet.payload))


async def test_a_dedicated_server_is_greeted_and_kept_alive_while_other_connections_stay_apart(
    server_url: str,
) -> None:
    async with connect(server_url, subprotocols=[Subprotocol(SUBPROTOCOL)]) as websocket:
        assert websocket.subprotocol == SUBPROTOCOL
        await websocket.send(open_frame(SERVER_FESL, "bfbc2-pc-server.fesl.ea.com", 19021, 50001))
        await send(websocket, SERVER_FESL, Packet("fsys", 0xC0, 1, format_fields(SERVER_HELLO)))

        key, answer, fields = await receive(websocket)
        assert (key, answer.command, answer.kind, answer.number) == (SERVER_FESL, "fsys", 0x80, 1)
        assert fields["TXN"] == "Hello"
        assert fields["theaterIp"] == "bfbc2-pc-server.theater.ea.com"
        assert fields["theaterPort"] == "18326"
        assert (fields["domainPartition.domain"], fields["domainPartition.subDomain"]) == (
            "eagames",
            "BFBC2",
        )
        assert fields["activityTimeoutSecs"] == "0"
        assert fields["curTime"].endswith(" UTC")

        key, push, fields = await receive(websocket)
        assert (key, push.command, push.number) == (SERVER_FESL, "fsys", 0)
        assert (fields["TXN"], fields["memcheck.[]"]) == ("MemCheck", "0")

        reply = format_fields([("TXN", "MemCheck"), ("result", "")])
        await send(websocket, SERVER_FESL, Packet("fsys", 0x80, 0, reply))
        await websocket.send(open_frame(OTHER_FESL, "unknown-pc.fesl.ea.com", 18390, 50002))
        unknown = format_fields([("TXN", "Hello"), ("clientString", "unknown-pc")])
        await send(websocket, OTHER_FESL, Packet("fsys", 0xC0, 1, unknown))

        key, answer, fields = await receive(websocket)
        assert (key, answer.number, fields["TXN"]) == (OTHER_FESL, 1, "Hello")
        assert fields["errorCode"] != "0"

        nothing = format_fields([("TXN", "Nothing")])
        await send(websocket, SERVER_FESL, Packet("zzzz", 0xC0, 2, nothing))
        key, answer, fields = await receive(websocket)
        assert (key, answer.number, fields["TXN"]) == (SERVER_FESL, 2, "Nothing")
        assert fields["errorCode"] != "0"


async def test_an_unexpected_failure_answers_an_error_and_keeps_the_connection() -> None:
    async def greet(connection: FeslConnection, request: Hello) -> None:
        connection.hello = request

    async def fail(connection: FeslConnection, request: Message) -> None:
        raise ConnectionRefusedError("the database is unreachable")

    outbox = Mock()
    routes: dict[tuple[str, str], Route[Any]] = {
        ("fsys", "Hello"): Route(Hello, greet),
        ("fsys", "GetPingSites"): Route(Message, fail),
        ("acct", "NuLogin"): Route(Message, fail),
    }
    connection = FeslConnection(cast("GameSession", None), outbox, routes)
    await connection.receive(Packet("fsys", 0xC0, 1, format_fields(SERVER_HELLO)))
    await connection.receive(Packet("fsys", 0xC0, 2, format_fields([("TXN", "GetPingSites")])))
    await connection.receive(Packet("acct", 0xC0, 3, format_fields([("TXN", "NuLogin")])))

    answers = [call.args[0] for call in outbox.send.call_args_list]
    # The game's login dialog reads 99 as a key server timeout, so the account service answers
    # its own code for a failed service.
    assert [
        (answer.number, dict(parse_fields(answer.payload))["errorCode"]) for answer in answers
    ] == [(2, "99"), (3, "193")]
    outbox.close.assert_not_called()


async def test_a_websocket_without_the_relay_subprotocol_is_refused(server_url: str) -> None:
    with pytest.raises(InvalidStatus) as refused:
        async with connect(server_url):
            pass

    assert refused.value.response.status_code == 403


@pytest.mark.parametrize(
    ("message", "code"),
    [
        pytest.param("hello", 1003, id="text message"),
        pytest.param(bytes.fromhex("09010001"), 1002, id="unknown frame type"),
        pytest.param(bytes.fromhex("02010001 66737973"), 1002, id="data that is not a packet"),
    ],
)
async def test_messages_that_break_the_relay_protocol_end_the_session(
    server_url: str, message: str | bytes, code: int
) -> None:
    async with connect(server_url, subprotocols=[Subprotocol(SUBPROTOCOL)]) as websocket:
        await websocket.send(message)
        with pytest.raises(ConnectionClosed) as closed:
            await websocket.recv()

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == code


async def test_a_connection_that_no_service_answers_is_closed(server_url: str) -> None:
    async with connect(server_url, subprotocols=[Subprotocol(SUBPROTOCOL)]) as websocket:
        await websocket.send(bytes.fromhex("01020001 47DBC350 0F") + b"pc.theater.test")

        assert await websocket.recv(decode=False) == bytes.fromhex("03020001")


async def test_stopping_the_server_ends_sessions_with_a_close_code_windows_accepts(
    relay_server: tuple[str, RelayServer],
) -> None:
    url, server = relay_server
    async with connect(url, subprotocols=[Subprotocol(SUBPROTOCOL)]) as hook:
        server.should_exit = True
        with pytest.raises(ConnectionClosed) as closed:
            await hook.recv()

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 1001
