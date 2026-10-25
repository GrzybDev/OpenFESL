import argparse
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openfesl.core.accounts import Account, AccountState
from openfesl.core.admin import AdminAction
from openfesl.core.games import Game, Games, load_games
from openfesl.core.personas import Persona, PersonaState
from openfesl.core.policies import GamePolicies, setting_key
from openfesl.core.servers import (
    GameServer,
    HostingMode,
    ServerState,
    new_provider,
    provider_file,
)
from openfesl.server.settings import Settings
from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.servers import SqlServerStore
from openfesl.storage.settings import SqlSettingStore


async def run(settings: Settings, args: argparse.Namespace) -> None:
    """Run one of the admin's commands as the command line names it.

    Args:
        settings: The deployment's settings.
        args: The parsed command line.
    """
    match args.command:
        case "keys":
            await _keys(settings, args)
        case "hosting":
            print(await _hosting(settings, args.mode))
        case "providers":
            await _providers(settings, args)
        case "servers":
            await _servers(settings, args)
        case "settings":
            await _settings(settings, args)
        case "accounts":
            await _accounts(settings, args)
        case "personas":
            await _personas(settings, args)
        case "kick":
            await _kick(settings, args)


@asynccontextmanager
async def _database(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    try:
        await migrate(engine)
        yield async_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _keys(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlEntitlementStore(sessions)
        if args.action == "import":
            print(f"Added {await store.import_keys(load_games().keys)} product keys")
            return
        limits = await store.limits(args.code)
        if limits is None:
            sys.exit(f"No product key is {args.code}")
        once = args.once_per_account
        if args.unlimited or args.max_uses or once is not None:
            most = None if args.unlimited else args.max_uses or limits.max_uses
            once = limits.once_per_account if once is None else once
            await store.limit(limits.code, most, once, admin=None)
            limits = replace(limits, max_uses=most, once_per_account=once)
        ceiling = limits.max_uses or "unlimited"
        again = "once" if limits.once_per_account else "any number of times"
        print(f"{limits.code}  uses {limits.uses} of {ceiling}  {again} per account")


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
                if not await store.set_state(args.ugid, state, admin=None):
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


async def _personas(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlPersonaStore(sessions)
        persona = await _player_persona(store, args.name)
        await store.set_state(persona.id, PersonaState(args.state), admin=None)


async def _kick(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        persona = await _player_persona(SqlPersonaStore(sessions), args.name)
        await SqlAdminLog(sessions).record(AdminAction.PLAYER_KICK, str(persona.id), admin=None)


async def _player_persona(store: SqlPersonaStore, name: str) -> Persona:
    match await store.named(name):
        case []:
            sys.exit(f"No player's persona is called {name}")
        case [persona]:
            return persona
        case _:
            sys.exit(f"Personas of several games are called {name}")
