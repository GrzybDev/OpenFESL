import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import anyio
import pytest
from accounts import register
from aiosmtpd.smtp import SMTP
from identity import IdentityProvider, identity_provider
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Docker Desktop on Windows publishes container ports on 127.0.0.1, not on the name localhost.
os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")

from playwright.async_api import Browser, Error, async_playwright
from support import FESL, PUBLIC_URL, Mailbox, game
from testcontainers.community.postgres import PostgresContainer

from openfesl.core import admin, history, trust
from openfesl.core.games import Game, load_games
from openfesl.server.cli import RelayServer, main, uvicorn_config
from openfesl.storage.database import migrate
from openfesl.storage.history import SqlHistoryStore
from openfesl.storage.store import SqlProductStore


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
            "activity_hour, activity_day, game_icon, catalogue"
        )
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        # The stores' products and bundles, numbered from 1 again.
        await connection.execute(text("TRUNCATE store_product, store_bundle, store_box CASCADE"))
        # The stats that rewards' goals count from, which belong to no account.
        await connection.execute(text("TRUNCATE reward_baseline"))
        await connection.execute(text("ALTER SEQUENCE store_id RESTART"))
    await engine.dispose()


@pytest.fixture
def server_url(relay_server: tuple[str, RelayServer]) -> str:
    return relay_server[0]


@pytest.fixture
async def catalogue_sessions(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Open sessions of the database, whose imported catalogues go once the test ends."""
    engine = create_async_engine(database_url)
    await migrate(engine)
    yield async_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE catalogue, admin_action CASCADE"))
    await engine.dispose()


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
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Name the folder of the deployment's files, before a server starts with it."""
    folder = tmp_path / "files"
    monkeypatch.setenv("FILES_PATH", str(folder))
    return folder


@pytest.fixture
async def edge() -> AsyncIterator[Browser]:
    """Start Microsoft Edge without a window, which runs the pages' scripts."""
    async with async_playwright() as playwright:
        try:
            # WebGL drawn in software, as on machines without a graphics card.
            browser = await playwright.chromium.launch(
                channel="msedge", args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
            )
        except Error:
            pytest.skip("Microsoft Edge, which runs the pages' scripts, is not installed")
        yield browser
        await browser.close()


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
async def products(database_url: str) -> AsyncIterator[SqlProductStore]:
    # Bundles are added through the store's service; no page adds them yet.
    engine = create_async_engine(database_url)
    yield SqlProductStore(async_sessionmaker(engine))
    await engine.dispose()


@pytest.fixture(scope="session")
def bfheroes() -> Game:
    game = load_games().by_id("bfheroes")
    assert game is not None
    return game


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


@pytest.fixture
async def services(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[IdentityProvider, IdentityProvider]]:
    # Before the server starts, which reads its sign-in services once: an OpenID Connect service
    # and a plain OAuth2 one that lists the account's addresses, as GitHub does.
    async with identity_provider() as connect, identity_provider() as plain:
        configured = [
            {
                "id": "connect",
                "name": "Invented Connect",
                "glyph": "globe",
                "issuer": connect.url,
                "client_id": "site-client",
                "client_secret": "invented-secret",
                "scope": "openid email profile",
            },
            {
                "id": "plain",
                "name": "Invented Plain",
                "kind": "oauth2",
                "glyph": "code",
                "authorize_url": f"{plain.url}/oauth/authorize",
                "token_url": f"{plain.url}/oauth/token",
                "userinfo_url": f"{plain.url}/api/user",
                "emails_url": f"{plain.url}/api/emails",
                "client_id": "site-client",
                "client_secret": "invented-secret",
                "scope": "read:user user:email",
                "subject_claim": "id",
                "name_claim": "login",
            },
        ]
        monkeypatch.setenv("SIGN_IN_SERVICES", json.dumps(configured))
        yield connect, plain
