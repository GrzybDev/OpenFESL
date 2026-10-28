import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from openfesl.core.games import Games, load_games
from openfesl.relay.endpoint import relay_endpoint
from openfesl.server.admin import include_admin_routes
from openfesl.server.public import include_public_routes
from openfesl.server.services import GameServices
from openfesl.server.settings import Settings
from openfesl.server.stores import Stores
from openfesl.storage.database import migrate
from openfesl.web.account.session import PlayerSessionMiddleware
from openfesl.web.game_http import GameHttpMiddleware
from openfesl.web.languages import LanguageMiddleware
from openfesl.web.magma import magma_app
from openfesl.web.magma_servers import servers_router
from openfesl.web.magma_store import store_router
from openfesl.web.pages import STATIC, Pages, StaticAssets
from openfesl.web.security import SecurityHeadersMiddleware
from openfesl.web.site import SiteBarMiddleware

# The session cookie outlives a session, so that an expired one can say so.
SESSION_COOKIE_AGE = 14 * 24 * 3600


def create_app(settings: Settings | None = None) -> FastAPI:
    """Join the layers into the ASGI application that Uvicorn serves.

    The database schema is brought up to date before the first connection is accepted.

    Args:
        settings: The deployment's settings; read from the environment when omitted.
    """
    settings = settings or Settings()
    games = load_games()
    # The engine connects on first use, inside the lifespan.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    stores = Stores(async_sessionmaker(engine))
    services = GameServices(settings, games, stores)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await migrate(engine)
        # A new database starts with the games' keys; from then on the admin manages them.
        if not await stores.keys.has_keys():
            await stores.keys.import_keys(games.keys)
        async with services.running(app):
            yield
        await engine.dispose()

    pages = Pages(games, services.census, settings.turnstile is not None)
    app = FastAPI(
        title="OpenFESL",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        exception_handlers={HTTPException: pages.not_found, Exception: pages.failure},
    )
    app.add_api_websocket_route("/", relay_endpoint(services.start_session))
    app.mount("/static", StaticAssets(directory=STATIC))
    include_public_routes(app, settings, games, pages, services, stores)
    include_admin_routes(app, settings, games, pages, services, stores)
    _add_middleware(app, settings, games, services, stores)

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    return app


def _add_middleware(
    app: FastAPI, settings: Settings, games: Games, services: GameServices, stores: Stores
) -> None:
    # The last one added sees a request first: the game's HTTP keeps its own answers. Only the
    # sign-ins and their forms store anything in the session, so other pages set no cookie.
    secret = settings.session_secret.get_secret_value() if settings.session_secret else None
    app.add_middleware(
        SiteBarMiddleware, games=games, wallets=services.wallets, heroes=services.heroes
    )
    app.add_middleware(PlayerSessionMiddleware, accounts=stores.accounts.account)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret or secrets.token_urlsafe(32),
        session_cookie="openfesl_session",
        max_age=SESSION_COOKIE_AGE,
        same_site="lax",
        # Browsers send a Secure cookie only over HTTPS, as a deployment serves the pages.
        https_only=(settings.public_url or "").startswith("https://"),
    )
    app.add_middleware(LanguageMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    magma = {
        game.http.magma: magma_app(
            game,
            services.magma,
            [
                servers_router(game, services.magma),
                store_router(game, services.magma, services.store, services.purchases),
            ],
        )
        for game in games
        if game.http.magma
    }
    app.add_middleware(GameHttpMiddleware, http=services.game_http, magma=magma)
