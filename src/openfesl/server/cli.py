import argparse
import asyncio
import logging
import socket
import sys
import urllib.request
from collections.abc import Sequence

import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.games import load_games
from openfesl.protocol.packet import MAX_SIZE
from openfesl.relay.endpoint import close_sessions
from openfesl.server.settings import Settings
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore

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


async def _import_keys(settings: Settings) -> int:
    engine = create_async_engine(settings.database_url)
    try:
        await migrate(engine)
        return await SqlEntitlementStore(async_sessionmaker(engine)).import_keys(load_games().keys)
    finally:
        await engine.dispose()


def _healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            status: int = response.status
    except OSError:
        return False
    return status == 200
