from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from openfesl.core.account_settings import AccountSettingsService
from openfesl.core.accounts import AccountService
from openfesl.core.connect import connect_routes
from openfesl.core.entitlements import EntitlementService
from openfesl.core.fesl import Route
from openfesl.core.game_http import GameHttp
from openfesl.core.games import load_games
from openfesl.core.logins import Logins
from openfesl.core.personas import PersonaService
from openfesl.core.registration import Terms
from openfesl.core.session import GameSession, Players
from openfesl.core.telemetry import telemetry_routes
from openfesl.core.texts import load_texts
from openfesl.relay.endpoint import relay_endpoint
from openfesl.server.settings import Settings
from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.personas import SqlPersonaStore
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
        accounts = AccountService(store, logins, entitlements, Terms.load())
        personas = PersonaService(SqlPersonaStore(sessions))
        await personas.prepare(await accounts.prepare(games))
        routes.update(connect_routes(games))
        routes.update(telemetry_routes())
        routes.update(accounts.routes())
        routes.update(AccountSettingsService(store, logins).routes())
        routes.update(personas.routes())
        routes.update(EntitlementService(entitlements).routes())
        routes.update(game_http.routes())
        yield
        await engine.dispose()

    def start_session(websocket: WebSocket) -> GameSession:
        return GameSession(routes, players, settings.client_address(websocket))

    app = FastAPI(
        title="OpenFESL", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_api_websocket_route("/", relay_endpoint(start_session))
    app.add_middleware(GameHttpMiddleware, http=game_http)

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    return app
