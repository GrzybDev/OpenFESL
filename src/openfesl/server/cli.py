import argparse
import asyncio
import logging
import socket
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openfesl.core.accounts import Account, AccountState
from openfesl.core.games import Game, Games, load_games
from openfesl.core.personas import PersonaState
from openfesl.core.policies import GamePolicies, setting_key
from openfesl.core.servers import (
    GameServer,
    HostingMode,
    ServerState,
    new_provider,
    provider_file,
)
from openfesl.protocol.packet import MAX_SIZE
from openfesl.relay.endpoint import close_sessions
from openfesl.server.health import DEFAULT_PORT
from openfesl.server.settings import Settings
from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.servers import SqlServerStore
from openfesl.storage.settings import SqlSettingStore

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

    match args.command:
        case "serve":
            settings = Settings()
            logging.basicConfig(
                level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
            )
            RelayServer(uvicorn_config(args.host, args.port)).run()
        case "keys":
            print(f"Added {asyncio.run(_import_keys(Settings()))} product keys")
        case "hosting":
            print(asyncio.run(_hosting(Settings(), args.mode)))
        case "providers":
            asyncio.run(_providers(Settings(), args))
        case "servers":
            asyncio.run(_servers(Settings(), args))
        case "settings":
            asyncio.run(_settings(Settings(), args))
        case "accounts":
            asyncio.run(_accounts(Settings(), args))
        case "personas":
            asyncio.run(_personas(Settings(), args))


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
                except (ValueError, OSError) as error:
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


async def _settings(settings: Settings, args: argparse.Namespace) -> None:
    games = load_games()
    async with _database(settings) as sessions:
        store = SqlSettingStore(sessions)
        policies = GamePolicies(store)
        if args.action == "list":
            for game in games:
                current = await policies.of(game)
                for name in game.policies.names():
                    value, default = current.text(name), game.policies.text(name)
                    line = f"{setting_key(game, name)}  {value}"
                    print(line if value == default else f"{line}  (default {default})")
            return
        game, name = _policy(games, args.key)
        match args.action:
            case "set":
                try:
                    value = game.policies.overridden(name, args.value).text(name)
                except ValueError:
                    sys.exit(f"{args.key} takes no value {args.value!r}")
                await store.change(args.key, value, admin=None)
            case "reset":
                await store.change(args.key, None, admin=None)
        print((await policies.of(game)).text(name))


def _policy(games: Games, key: str) -> tuple[Game, str]:
    game_id, _, name = key.partition(".")
    game = games.by_id(game_id)
    if game is None or name not in game.policies.names():
        sys.exit(f"No setting is called {key}")
    return game, name


async def _accounts(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlAccountStore(sessions)
        account = await _player_account(store, args.login)
        match args.action:
            case "state":
                state = AccountState(args.state)
                if state is AccountState.ACTIVE and args.until:
                    sys.exit("An active account has no end date")
                await store.set_state(account.id, state, args.until, admin=None)
            case "verify":
                await store.confirm_account(account.id, admin=None)


async def _player_account(store: SqlAccountStore, login: str) -> Account:
    credentials = await store.find(login)
    # Every dedicated server of a game logs in with that account, so it is no player's.
    if credentials is None or credentials.account.server:
        sys.exit(f"No player's account logs in as {login}")
    return credentials.account


def _moment(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is no date such as 2026-12-01") from None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    if moment <= datetime.now(UTC):
        raise argparse.ArgumentTypeError(f"{text} has passed")
    return moment


async def _personas(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlPersonaStore(sessions)
        match await store.named(args.name):
            case []:
                sys.exit(f"No player's persona is called {args.name}")
            case [persona]:
                await store.set_state(persona.id, PersonaState(args.state), admin=None)
            case _:
                sys.exit(f"Personas of several games are called {args.name}")
