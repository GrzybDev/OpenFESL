import argparse
import asyncio
import logging
import socket
import sys
import urllib.request
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openfesl.core.games import load_games
from openfesl.core.servers import (
    GameServer,
    HostingMode,
    ServerState,
    new_provider,
    provider_file,
)
from openfesl.protocol.packet import MAX_SIZE
from openfesl.relay.endpoint import close_sessions
from openfesl.server.settings import Settings
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.servers import SqlServerStore

DEFAULT_PORT = 8000
GOING_AWAY = 1001

# One relay frame: its 4-byte header and the largest packet the hook forwards.
WEBSOCKET_MAX_SIZE = 4 + MAX_SIZE


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command named on the command line.

    Args:
        argv: The arguments after the program name; the process's own when omitted.
    """
    parser = argparse.ArgumentParser(
        prog="openfesl",
        description="Master server for games built on EA's FESL and Theater services.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the server")
    serve.add_argument("--host", default="0.0.0.0", help="address to listen on")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    health = commands.add_parser("health", help="check that the server on this machine answers")
    health.add_argument("--port", type=int, default=DEFAULT_PORT, help="port the server uses")
    keys = commands.add_parser("keys", help="manage product keys")
    keys.add_subparsers(dest="action", required=True).add_parser(
        "import", help="add the games' product keys that the database lacks, keeping the others"
    )
    hosting = commands.add_parser(
        "hosting", help="show or change whether players see only approved servers' games"
    )
    hosting.add_argument("mode", nargs="?", choices=[mode.value for mode in HostingMode])
    providers = commands.add_parser("providers", help="manage the operators of dedicated servers")
    provider_actions = providers.add_subparsers(dest="action", required=True)
    provider_actions.add_parser("list", help="list the providers and their ids")
    add = provider_actions.add_parser(
        "add", help="add a provider and write the ProviderId.dat its servers read"
    )
    add.add_argument("name", help="what to call the provider; game lists show it")
    add.add_argument(
        "--out",
        type=Path,
        default=Path("ProviderId.dat"),
        help="the file to write, ProviderId.dat here by default; an existing one is kept",
    )
    provider_actions.add_parser(
        "revoke", help="stop every server of a provider from hosting"
    ).add_argument("id", help="the provider's id as listed")
    servers = commands.add_parser("servers", help="manage the dedicated servers that host")
    server_actions = servers.add_subparsers(dest="action", required=True)
    server_actions.add_parser("list", help="list the servers that ever hosted")
    server_actions.add_parser("approve", help="let players see a server's games").add_argument(
        "ugid"
    )
    server_actions.add_parser("revoke", help="stop a server from hosting").add_argument("ugid")
    args = parser.parse_args(argv)

    match args.command:
        case "serve":
            settings = Settings()
            logging.basicConfig(
                level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
            )
            RelayServer(uvicorn_config(args.host, args.port)).run()
        case "health":
            sys.exit(0 if _healthy(args.port) else 1)
        case "keys":
            print(f"Added {asyncio.run(_import_keys(Settings()))} product keys")
        case "hosting":
            print(asyncio.run(_hosting(Settings(), args.mode)))
        case "providers":
            asyncio.run(_providers(Settings(), args))
        case "servers":
            asyncio.run(_servers(Settings(), args))


class RelayServer(uvicorn.Server):
    """Uvicorn, ending relay sessions with a close code the hook can read before it stops."""

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """End every relay session with 1001, then stop as Uvicorn does.

        Uvicorn would close them with 1012, which Windows' WebSocket stack under the hook's
        WinHTTP refuses, so the hook could not tell a restart from a broken connection.

        Args:
            sockets: The listening sockets Uvicorn was given, if any.
        """
        await close_sessions(GOING_AWAY)
        await super().shutdown(sockets)


def uvicorn_config(host: str, port: int) -> uvicorn.Config:
    """Return Uvicorn's settings for the server.

    One worker holds every session, since sessions reach each other in memory. Server pings
    find dead peers, as the game sends nothing of its own on a quiet connection, and client IPs
    come from the trusted proxy's header in the settings rather than Uvicorn's.

    Args:
        host: The address to listen on.
        port: The port to listen on.
    """
    return uvicorn.Config(
        "openfesl.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        workers=1,
        ws="websockets-sansio",
        ws_max_size=WEBSOCKET_MAX_SIZE,
        ws_per_message_deflate=False,
        proxy_headers=False,
        timeout_graceful_shutdown=5,
        log_config=None,
        access_log=False,
    )


@asynccontextmanager
async def _database(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    try:
        await migrate(engine)
        yield async_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _import_keys(settings: Settings) -> int:
    async with _database(settings) as sessions:
        return await SqlEntitlementStore(sessions).import_keys(load_games().keys)


async def _hosting(settings: Settings, mode: str | None) -> HostingMode:
    async with _database(settings) as sessions:
        store = SqlServerStore(sessions)
        if mode:
            await store.set_hosting_mode(HostingMode(mode))
        return await store.hosting_mode()


async def _providers(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlServerStore(sessions)
        match args.action:
            case "list":
                for provider in await store.providers():
                    state = "revoked" if provider.revoked else "active"
                    print(f"{provider.id.hex().upper()}  {state}  {provider.name}")
            case "add":
                try:
                    provider = new_provider(args.name)
                    # An operator may hold the file already, so none is ever replaced.
                    with args.out.open("xb") as file:
                        file.write(provider_file(provider.id))
                except (ValueError, FileExistsError) as error:
                    sys.exit(str(error))
                await store.add_provider(provider)
                print(provider.id.hex().upper())
            case "revoke":
                try:
                    provider_id = bytes.fromhex(args.id)
                except ValueError:
                    provider_id = b""
                if not await store.revoke_provider(provider_id):
                    sys.exit(f"No provider has the id {args.id}")


async def _servers(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlServerStore(sessions)
        match args.action:
            case "list":
                for server in await store.all():
                    state = ServerState.REVOKED if server.revoked else server.state
                    print(f"{server.ugid}  {state}  {_provider_of(server)}  {server.name}")
            case "approve" | "revoke":
                state = ServerState.APPROVED if args.action == "approve" else ServerState.REVOKED
                if not await store.set_state(args.ugid, state):
                    sys.exit(f"No server has the id {args.ugid}")


def _provider_of(server: GameServer) -> str:
    if server.provider is not None:
        return server.provider.name
    return server.provider_id.hex().upper() if server.provider_id else "-"


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            status: int = response.status
    except OSError:
        return False
    return status == 200
