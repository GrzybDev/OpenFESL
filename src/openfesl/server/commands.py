import argparse
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from openfesl.core.accounts import Account, AccountState
from openfesl.core.admin import AdminAction
from openfesl.core.catalogues import Catalogues
from openfesl.core.game_icons import icon_png
from openfesl.core.games import Game, Games, load_games
from openfesl.core.personas import Persona, PersonaState
from openfesl.core.policies import SITE_CONFIRMATION, GamePolicies, setting_key
from openfesl.core.servers import (
    GameServer,
    HostingMode,
    ServerState,
    new_provider,
    provider_file,
)
from openfesl.extraction import ExtractionError, extract, find_game
from openfesl.server.settings import Settings
from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.catalogues import IconFolder, SqlCatalogueStore
from openfesl.storage.database import migrate
from openfesl.storage.game_icons import SqlGameIconStore
from openfesl.storage.keys import SqlKeyStore
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
        case "icons":
            await _icons(settings, args)
        case "catalogue":
            await _import_catalogue(settings, args.file)


def extract_catalogue(folder: Path, out: Path | None) -> None:
    """Write the catalogue of the game whose copy a folder holds into a file for the server.

    It needs no server and no database, so that players run it on their own computers.

    Args:
        folder: The folder of the copy's executable.
        out: The file to write; <game>-catalogue.zip in the current folder when omitted.
    """
    try:
        game = find_game(load_games(), folder)
        catalogue = extract(game, folder)
    except ExtractionError as error:
        sys.exit(str(error))
    out = out or Path(f"{game.id}-catalogue.zip")
    try:
        out.write_bytes(catalogue.write())
    except OSError as error:
        sys.exit(str(error))
    print(
        f"Wrote the catalogue of {game.name} to {out}: {len(catalogue.document.items)} items, "
        f"texts in {len(catalogue.texts)} languages and {len(catalogue.icons)} icons"
    )


@asynccontextmanager
async def _database(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)
    try:
        await migrate(engine)
        yield async_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _import_catalogue(settings: Settings, file: Path) -> None:
    try:
        data = file.read_bytes()
    except OSError as error:
        sys.exit(str(error))
    async with _database(settings) as sessions:
        icons = IconFolder(settings.files_path) if settings.files_path else None
        catalogues = Catalogues(SqlCatalogueStore(sessions), icons, load_games())
        try:
            imported = await catalogues.import_file(data, admin=None)
        except ValueError as error:
            sys.exit(f"{file} holds no catalogue that this server imports: {error}")
    kept = f"{imported.icons} icons" if icons else "no icons, as FILES_PATH names no folder"
    print(
        f"Imported the catalogue of {imported.game.name}: {imported.items} items, "
        f"texts in {imported.languages} languages and {kept}"
    )


async def _keys(settings: Settings, args: argparse.Namespace) -> None:
    async with _database(settings) as sessions:
        store = SqlKeyStore(sessions)
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
            await store.set_hosting_mode(HostingMode(mode), admin=None)
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
                if not await store.add_provider(provider, admin=None):
                    args.out.unlink()
                    sys.exit(f"A provider named {args.name} exists already")
                print(provider.id.hex().upper())
            case "revoke":
                try:
                    provider_id = bytes.fromhex(args.id)
                except ValueError:
                    provider_id = b""
                if not await store.revoke_provider(provider_id, admin=None):
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
            site = _switch(await policies.site_confirmation())
            print(f"{SITE_CONFIRMATION}  {site}" + ("" if site == "on" else "  (default on)"))
            for game in games:
                current = await policies.of(game)
                for name in game.policies.names():
                    value, default = current.text(name), game.policies.text(name)
                    line = f"{setting_key(game, name)}  {value}"
                    print(line if value == default else f"{line}  (default {default})")
            return
        if args.key == SITE_CONFIRMATION:
            if args.action == "set" and args.value not in ("on", "off"):
                sys.exit(f"{args.key} takes on or off, not {args.value!r}")
            if args.action != "get":
                on = args.action == "reset" or args.value == "on"
                await policies.set_site_confirmation(on, admin=None)
            print(_switch(await policies.site_confirmation()))
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


def _switch(on: bool) -> str:
    return "on" if on else "off"


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


async def _icons(settings: Settings, args: argparse.Namespace) -> None:
    game = load_games().by_id(args.game)
    if game is None:
        sys.exit(f"No game has the id {args.game}")
    async with _database(settings) as sessions:
        store = SqlGameIconStore(sessions)
        match args.action:
            case "set":
                try:
                    png = icon_png(args.file.read_bytes())
                except (ValueError, OSError) as error:
                    sys.exit(str(error))
                await store.store(game.id, png, admin=None)
            case "remove":
                if not await store.remove(game.id, admin=None):
                    sys.exit(f"{game.name} has no icon")


async def _player_persona(store: SqlPersonaStore, name: str) -> Persona:
    match await store.named(name):
        case []:
            sys.exit(f"No player's persona is called {name}")
        case [persona]:
            return persona
        case _:
            sys.exit(f"Personas of several games are called {name}")
