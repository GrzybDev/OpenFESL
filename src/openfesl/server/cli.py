import argparse
import asyncio
import logging
import socket
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from openfesl.core.accounts import AccountState
from openfesl.core.personas import PersonaState
from openfesl.core.servers import HostingMode
from openfesl.protocol.packet import MAX_SIZE
from openfesl.relay.endpoint import close_sessions
from openfesl.server.commands import run
from openfesl.server.health import DEFAULT_PORT
from openfesl.server.settings import Settings

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
    setting_actions = commands.add_parser(
        "settings", help="show or override the games' policies"
    ).add_subparsers(dest="action", required=True)
    setting_actions.add_parser("list", help="list every policy of every game with its value")
    key_help = "the game and the policy, such as bfbc2.accounts.concurrent_login"
    setting_actions.add_parser("get", help="show a policy's value").add_argument(
        "key", help=key_help
    )
    overridden = setting_actions.add_parser("set", help="override a policy's default")
    overridden.add_argument("key", help=key_help)
    overridden.add_argument("value", help="the policy's new value")
    setting_actions.add_parser("reset", help="return a policy to its default").add_argument(
        "key", help=key_help
    )
    account_actions = commands.add_parser(
        "accounts", help="manage players' accounts"
    ).add_subparsers(dest="action", required=True)
    account_state = account_actions.add_parser(
        "state", help="set whether an account may log in; a player online is signed out"
    )
    account_state.add_argument("login", help="the name the account logs in with")
    account_state.add_argument("state", choices=[state.value for state in AccountState])
    account_state.add_argument(
        "--until",
        type=_moment,
        help="when the state ends, such as 2026-12-01 or 2026-12-01T18:00+01:00 (UTC by default)",
    )
    account_actions.add_parser(
        "verify", help="confirm an account's e-mail address so that it may log in"
    ).add_argument("login", help="the name the account logs in with")
    persona_state = (
        commands.add_parser("personas", help="manage players' personas")
        .add_subparsers(dest="action", required=True)
        .add_parser("state", help="set whether a persona may play; its name stays taken")
    )
    persona_state.add_argument("name", help="the persona's name")
    persona_state.add_argument("state", choices=[state.value for state in PersonaState])
    args = parser.parse_args(argv)

    settings = Settings()
    if args.command != "serve":
        asyncio.run(run(settings, args))
        return
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    RelayServer(uvicorn_config(args.host, args.port)).run()


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


def _moment(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is no date such as 2026-12-01") from None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    if moment <= datetime.now(UTC):
        raise argparse.ArgumentTypeError(f"{text} has passed")
    return moment
