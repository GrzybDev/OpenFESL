import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.account_settings import AccountSettingsService
from openfesl.core.accounts import AccountService
from openfesl.core.associations import AssociationService
from openfesl.core.browsing import Browsing
from openfesl.core.connect import connect_routes
from openfesl.core.entitlements import EntitlementService
from openfesl.core.fesl import Route
from openfesl.core.game_http import GameHttp
from openfesl.core.games import load_games
from openfesl.core.hosting import Hosting
from openfesl.core.joining import Joining
from openfesl.core.leaderboards import LeaderboardService
from openfesl.core.logins import Logins
from openfesl.core.messages import MessageService
from openfesl.core.personas import PersonaService
from openfesl.core.playnow import PlayNow
from openfesl.core.policies import GamePolicies
from openfesl.core.presence import PresenceService
from openfesl.core.records import RecordService
from openfesl.core.registration import Terms
from openfesl.core.servers import Approval
from openfesl.core.session import GameSession, Players
from openfesl.core.stats import StatsService
from openfesl.core.telemetry import telemetry_routes
from openfesl.core.texts import load_texts
from openfesl.core.theater import Theater, TheaterRoute, theater_routes
from openfesl.relay.endpoint import relay_endpoint
from openfesl.server.settings import Settings
from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.associations import SqlAssociationStore
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.messages import SqlMessageStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.records import SqlRecordStore
from openfesl.storage.servers import SqlServerStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.stats import SqlStatStore
from openfesl.web.game_http import GameHttpMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Join the layers into the ASGI application that Uvicorn serves.

    The database schema is brought up to date before the first connection is accepted.

    Args:
        settings: The deployment's settings; read from the environment when omitted.
    """
    settings = settings or Settings()
    routes: dict[tuple[str, str], Route[Any]] = {}
    players = Players()
    games = load_games()
    game_http = GameHttp(games, players, load_texts("news"))
    commands: dict[str, TheaterRoute[Any]] = {}
    theater = Theater(games, commands)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(
            settings.database_url, pool_pre_ping=True, hide_parameters=True
        )
        await migrate(engine)
        sessions = async_sessionmaker(engine)
        entitlements = SqlEntitlementStore(sessions)
        # A new database starts with the games' keys; from then on the admin manages them.
        if not await entitlements.has_keys():
            await entitlements.import_keys(games.keys)
        store = SqlAccountStore(sessions)
        logins = Logins(store)
        policies = GamePolicies(SqlSettingStore(sessions))
        accounts = AccountService(store, logins, entitlements, Terms.load(), policies)
        persona_store = SqlPersonaStore(sessions)
        personas = PersonaService(persona_store)
        await personas.prepare(await accounts.prepare(games))
        routes.update(connect_routes(games))
        routes.update(telemetry_routes())
        routes.update(accounts.routes())
        routes.update(AccountSettingsService(store, logins).routes())
        routes.update(personas.routes())
        hosting = Hosting(Approval(SqlServerStore(sessions)))
        routes.update(EntitlementService(entitlements, hosting).routes())
        routes.update(game_http.routes())
        stats = SqlStatStore(sessions)
        routes.update(StatsService(stats, persona_store, hosting).routes())
        routes.update(LeaderboardService(stats).routes())
        routes.update(RecordService(SqlRecordStore(sessions)).routes())
        associations = SqlAssociationStore(sessions)
        routes.update(AssociationService(associations, persona_store, players, hosting).routes())
        routes.update(PresenceService(persona_store, players, associations).routes())
        messages = MessageService(SqlMessageStore(sessions), persona_store, players, associations)
        routes.update(messages.routes())
        commands.update(theater_routes())
        commands.update(hosting.routes())
        commands.update(Browsing(hosting).routes())
        commands.update(Joining(hosting).routes())
        routes.update(PlayNow(hosting).routes())
        following = asyncio.create_task(hosting.follow_approvals())
        yield
        following.cancel()
        with suppress(asyncio.CancelledError):
            await following
        await messages.close()
        await engine.dispose()

    def start_session(websocket: WebSocket) -> GameSession:
        return GameSession(routes, players, settings.client_address(websocket), theater)

    app = FastAPI(
        title="OpenFESL", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_api_websocket_route("/", relay_endpoint(start_session))
    app.add_middleware(GameHttpMiddleware, http=game_http)

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    return app
