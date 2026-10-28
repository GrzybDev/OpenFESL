import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import anyio
import pytest
from accounts import register
from aiosmtpd.smtp import SMTP
from identity import IdentityProvider, identity_provider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Docker Desktop on Windows publishes container ports on 127.0.0.1, not on the name localhost.
os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")

from support import FESL, PUBLIC_URL, Mailbox, game
from testcontainers.community.postgres import PostgresContainer

from openfesl.core import admin, history, trust
from openfesl.server.cli import RelayServer, main, uvicorn_config
from openfesl.storage.database import migrate
from openfesl.storage.history import SqlHistoryStore


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
    # A plain URL, as database hosts such as Dokploy hand it out; the server picks its driver.
    monkeypatch.setenv("DATABASE_URL", database_url.replace("+asyncpg", "", 1))
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
        # The keys too, so that the next server starts with the game data's keys, unlimited.
        tables = (
            "account, stat, game_server, provider, setting, admin_action, product_key, "
            "activity_hour, activity_day, game_icon"
        )
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
def server_url(relay_server: tuple[str, RelayServer]) -> str:
    return relay_server[0]


@pytest.fixture
async def admin_provider(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IdentityProvider]:
    # Before the server starts, which reads its sign-in provider once. Members of fesl-admins
    # are admins, members of fesl-moderators moderators.
    async with identity_provider() as running:
        for key, value in {
            "ADMIN_OIDC_NAME": "Invented Sign-in",
            "ADMIN_OIDC_ISSUER": running.url,
            "ADMIN_OIDC_CLIENT_ID": "panel-client",
            "ADMIN_OIDC_CLIENT_SECRET": "invented-secret",
            "ADMIN_GROUP": "fesl-admins",
            "MODERATOR_GROUP": "fesl-moderators",
        }.items():
            monkeypatch.setenv(key, value)
        yield running


@pytest.fixture
def prompt_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The admin acts in another process; the server reads the log of actions this often.
    monkeypatch.setattr(admin, "ADMIN_CHECK_INTERVAL", 0.05)


@pytest.fixture
def prompt_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The admin decides in another process; the server reads the decisions back this often.
    monkeypatch.setattr(trust, "APPROVAL_CHECK_INTERVAL", 0.05)


@pytest.fixture
async def open_hosting(server_url: str) -> None:
    # Approval has tests of its own; elsewhere any dedicated server may host.
    await anyio.to_thread.run_sync(main, ["hosting", "open"])


@pytest.fixture
def quick_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before the server starts, whose first wait would otherwise last a minute.
    monkeypatch.setattr(history, "SAMPLE_INTERVAL", 0.05)


@pytest.fixture
async def history_store(database_url: str) -> AsyncIterator[SqlHistoryStore]:
    engine = create_async_engine(database_url)
    await migrate(engine)
    yield SqlHistoryStore(async_sessionmaker(engine))
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE activity_hour, activity_day"))
    await engine.dispose()


@pytest.fixture
async def mailbox(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Mailbox]:
    # Before the server starts, which reads its mail server from the environment.
    box = Mailbox()
    loop = asyncio.get_running_loop()
    server = await loop.create_server(lambda: SMTP(box), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    environment = {
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": str(port),
        "SMTP_SECURITY": "none",
        "SMTP_SENDER": "openfesl@example.test",
        "PUBLIC_URL": PUBLIC_URL,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    yield box
    server.close()
    await server.wait_closed()


@pytest.fixture
async def base(server_url: str) -> AsyncIterator[str]:
    """Return the server's web address once Bad Company 2 registered ann and bob."""
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        for address in ("ann@example.test", "bob@example.test"):
            await register(player, address)
    yield server_url.replace("ws://", "http://").rstrip("/")
