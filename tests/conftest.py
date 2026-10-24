import asyncio
from collections.abc import AsyncIterator

import pytest
import uvicorn

from openfesl.server.cli import uvicorn_config


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def server_url() -> AsyncIterator[str]:
    # A real Uvicorn with the production settings, so the handshake, subprotocol and frame
    # limits are the ones the hook meets.
    server = uvicorn.Server(uvicorn_config("127.0.0.1", 0))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}/"
    server.should_exit = True
    await task
