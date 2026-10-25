import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException

from openfesl.core.account_settings import AccountSettingsService
from openfesl.core.accounts import AccountService
from openfesl.core.admin import AdminFeed
from openfesl.core.associations import AssociationService
from openfesl.core.browsing import Browsing
from openfesl.core.census import Census
from openfesl.core.connect import connect_routes
from openfesl.core.entitlements import EntitlementService
from openfesl.core.fesl import Route
from openfesl.core.game_http import GameHttp
from openfesl.core.games import load_games
from openfesl.core.history import OnlineHistory
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
from openfesl.core.verification import Confirmation, EmailVerification
from openfesl.relay.endpoint import relay_endpoint
from openfesl.server.mail import SmtpMailer
from openfesl.server.settings import Settings
from openfesl.storage.accounts import SqlAccountStore
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.associations import SqlAssociationStore
from openfesl.storage.database import migrate
from openfesl.storage.entitlements import SqlEntitlementStore
from openfesl.storage.history import SqlHistoryStore
from openfesl.storage.messages import SqlMessageStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.records import SqlRecordStore
from openfesl.storage.servers import SqlServerStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.stats import SqlStatStore
from openfesl.web.front import front_routes
from openfesl.web.game_http import GameHttpMiddleware
from openfesl.web.host import host_routes
from openfesl.web.languages import LanguageMiddleware
from openfesl.web.leaderboard import leaderboard_routes
from openfesl.web.menu import menu_routes
from openfesl.web.pages import STATIC, Pages, StaticAssets
from openfesl.web.play import play_routes, relay_address
from openfesl.web.players import player_routes
from openfesl.web.security import SecurityHeadersMiddleware
from openfesl.web.servers import server_routes
from openfesl.web.shell import shell_routes
from openfesl.web.verification import verification_routes


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
    # The engine connects on first use, inside the lifespan.
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    sessions = async_sessionmaker(engine)
    approval = Approval(SqlServerStore(sessions))
    hosting = Hosting(approval)
    census = Census(hosting, players)
    history = OnlineHistory(SqlHistoryStore(sessions), census, games)
    stats = SqlStatStore(sessions)
    persona_store = SqlPersonaStore(sessions)
    records = SqlRecordStore(sessions)
    entitlements = SqlEntitlementStore(sessions)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await migrate(engine)
        # A new database starts with the games' keys; from then on the admin manages them.
        if not await entitlements.has_keys():
            await entitlements.import_keys(games.keys)
        store = SqlAccountStore(sessions)
        logins = Logins(store)
        policies = GamePolicies(SqlSettingStore(sessions))
        mailer = None
        if settings.smtp_host and settings.smtp_sender:
            mailer = SmtpMailer(settings, settings.smtp_host, settings.smtp_sender)
        verification = EmailVerification(store, mailer, settings.public_url)
        app.state.verification = verification
        accounts = AccountService(store, logins, entitlements, Terms.load(), policies, verification)
        personas = PersonaService(persona_store)
        await personas.prepare(await accounts.prepare(games))
        routes.update(connect_routes(games, policies))
        routes.update(telemetry_routes())
        routes.update(accounts.routes())
        routes.update(AccountSettingsService(store, logins, policies).routes())
        routes.update(personas.routes())
        routes.update(EntitlementService(entitlements, hosting).routes())
        routes.update(game_http.routes())
        routes.update(StatsService(stats, persona_store, hosting).routes())
        routes.update(LeaderboardService(stats).routes())
        routes.update(RecordService(records).routes())
        associations = SqlAssociationStore(sessions)
        routes.update(AssociationService(associations, persona_store, players, hosting).routes())
        routes.update(PresenceService(persona_store, players, associations).routes())
        messages = MessageService(SqlMessageStore(sessions), persona_store, players, associations)
        routes.update(messages.routes())
        commands.update(theater_routes())
        commands.update(hosting.routes())
        commands.update(Browsing(hosting).routes())
        joining = Joining(hosting)
        commands.update(joining.routes())
        routes.update(PlayNow(hosting).routes())
        admin_log = SqlAdminLog(sessions)
        feed = AdminFeed(admin_log)
        players.follow(feed)
        joining.follow(feed)
        followers = [
            asyncio.create_task(hosting.follow_approvals()),
            asyncio.create_task(feed.follow(await admin_log.last())),
            asyncio.create_task(history.follow()),
        ]
        yield
        for follower in followers:
            follower.cancel()
            with suppress(asyncio.CancelledError):
                await follower
        await messages.close()
        await engine.dispose()

    def start_session(websocket: WebSocket, hook_version: str | None) -> GameSession:
        address = settings.client_address(websocket)
        return GameSession(routes, players, address, theater, hook_version)

    pages = Pages(games, census)
    app = FastAPI(
        title="OpenFESL",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        exception_handlers={HTTPException: pages.not_found, Exception: pages.failure},
    )

    async def confirm(token: str) -> Confirmation:
        verification: EmailVerification = app.state.verification
        return await verification.confirm(token)

    app.add_api_websocket_route("/", relay_endpoint(start_session))
    app.mount("/static", StaticAssets(directory=STATIC))
    app.include_router(shell_routes(pages, Terms.load()))
    app.include_router(menu_routes(pages, games, census))
    app.include_router(front_routes(pages, games, census))
    app.include_router(server_routes(pages, games, census))
    app.include_router(leaderboard_routes(pages, games, census, stats))
    app.include_router(player_routes(pages, games, census, stats, persona_store, records))

    def relay(request: Request) -> str:
        return relay_address(request, settings.public_url)

    app.include_router(
        play_routes(pages, games, census, entitlements, relay, settings.hook_default_url)
    )
    app.include_router(
        host_routes(
            pages, games, census, approval, settings.contact_email, relay, settings.hook_default_url
        )
    )
    app.include_router(verification_routes(confirm, pages))
    # The last one added sees a request first: the game's HTTP keeps its own answers.
    app.add_middleware(LanguageMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(GameHttpMiddleware, http=game_http)

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    return app
