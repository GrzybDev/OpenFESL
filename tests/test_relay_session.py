import asyncio

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

from openfesl.protocol.relay import SUBPROTOCOL
from openfesl.server.cli import RelayServer, uvicorn_config

pytestmark = pytest.mark.anyio


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


async def test_stopping_the_server_ends_sessions_with_a_close_code_windows_accepts() -> None:
    server = RelayServer(uvicorn_config("127.0.0.1", 0))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    async with connect(f"ws://127.0.0.1:{port}/", subprotocols=[Subprotocol(SUBPROTOCOL)]) as hook:
        server.should_exit = True
        with pytest.raises(ConnectionClosed) as closed:
            await hook.recv()
    await task

    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == 1001
