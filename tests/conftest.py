import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Docker Desktop on Windows publishes container ports on 127.0.0.1, not on the name localhost.
os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")

from testcontainers.community.postgres import PostgresContainer

from openfesl.server.cli import RelayServer, uvicorn_config


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:18-alpine", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture
async def relay_server(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[str, RelayServer]]:
    # The production server and settings, so the handshake, subprotocol, frame limits and
    # shutdown are the ones the hook meets.
    monkeypatch.setenv("DATABASE_URL", database_url)
    server = RelayServer(uvicorn_config("127.0.0.1", 0))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}/", server
    server.should_exit = True
    await task
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE account, stat RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
def server_url(relay_server: tuple[str, RelayServer]) -> str:
    return relay_server[0]
