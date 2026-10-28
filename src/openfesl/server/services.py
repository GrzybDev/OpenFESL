import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, WebSocket

from openfesl.core.account_management import AccountManagement
from openfesl.core.account_settings import AccountSettingsService
from openfesl.core.accounts import AccountService
from openfesl.core.admin import AdminFeed
from openfesl.core.associations import AssociationService
from openfesl.core.browsing import Browsing
from openfesl.core.catalogues import Catalogues
from openfesl.core.census import Census
from openfesl.core.chests import Chests
from openfesl.core.connect import connect_routes
from openfesl.core.deals import HotDeals
from openfesl.core.display_names import DisplayNames
from openfesl.core.drops import Drops
from openfesl.core.entitlements import EntitlementService
from openfesl.core.fesl import Route
from openfesl.core.figures import Figures
from openfesl.core.game_http import GameHttp
from openfesl.core.games import Games
from openfesl.core.hero_items import OwnWrites
from openfesl.core.heroes import Heroes
from openfesl.core.history import OnlineHistory
from openfesl.core.hosting import Hosting
from openfesl.core.item_writes import ItemWrites
from openfesl.core.joining import Joining
from openfesl.core.keys import KeyService
from openfesl.core.launcher import LauncherService, LauncherSessions
from openfesl.core.leaderboards import LeaderboardService
from openfesl.core.limits import Attempts, Mailings
from openfesl.core.logins import Logins
from openfesl.core.magma import Magma
from openfesl.core.mail import Outbox
from openfesl.core.messages import MessageService
from openfesl.core.mugshots import Mugshots
from openfesl.core.password_resets import PasswordResets
from openfesl.core.personas import PersonaService
from openfesl.core.playnow import PlayNow
from openfesl.core.policies import GamePolicies
from openfesl.core.presence import PresenceService
from openfesl.core.proofs import ProofMail
from openfesl.core.purchases import Purchases
from openfesl.core.records import RecordService
from openfesl.core.registration import Terms
from openfesl.core.relationships import Relationships
from openfesl.core.relay_tokens import RelayTokens
from openfesl.core.rich_presence import RichPresence
from openfesl.core.servers import Approval
from openfesl.core.session import GameSession, Players
from openfesl.core.sign_in import WebSignIn
from openfesl.core.start_links import StartLinks
from openfesl.core.stats import StatsService
from openfesl.core.store import Store
from openfesl.core.store_items import StoreItems
from openfesl.core.store_window import StoreWindows
from openfesl.core.telemetry import telemetry_routes
from openfesl.core.texts import load_texts
from openfesl.core.theater import Theater, TheaterRoute, theater_routes
from openfesl.core.trust import ServerTrust
from openfesl.core.verification import EmailVerification
from openfesl.core.wallets import Wallets
from openfesl.core.web_registration import WebRegistration
from openfesl.server.mail import SmtpMailer
from openfesl.server.settings import Settings
from openfesl.server.stores import Stores
from openfesl.storage.catalogues import CatalogueFolder
from openfesl.web.game_icons import presence_art


class GameServices:
    """The games' side of the server: their sessions, the games they host and what they call."""

    def __init__(self, settings: Settings, games: Games, stores: Stores) -> None:
        """Build what the games' sessions and the pages share before the server starts.

        Args:
            settings: The deployment's settings.
            games: The games the server serves.
            stores: The database's stores.
        """
        self._settings = settings
        self._games = games
        self._stores = stores
        self._routes: dict[tuple[str, str], Route[Any]] = {}
        self._commands: dict[str, TheaterRoute[Any]] = {}
        self._theater = Theater(games, self._commands)
        self.players = Players()
        self._launcher_sessions = LauncherSessions(self.players)
        self.start_links = StartLinks()
        self.game_http = GameHttp(games, self.players, load_texts("news"))
        self.approval = Approval(stores.servers)
        self.hosting = Hosting(self.approval)
        self.trust = ServerTrust(self.hosting, self.approval)
        self.wallets = Wallets(stores.wallets)
        self.heroes = Heroes(stores.heroes, stores.stats)
        self._relay_tokens = RelayTokens()
        self.magma = Magma(
            self._launcher_sessions,
            self._relay_tokens,
            self.players,
            self.trust,
            stores.personas,
            stores.entitlements,
            self.wallets,
        )
        self.relationships = Relationships(
            stores.relationships, self.players, self.hosting, self.trust
        )
        self.mugshots = Mugshots(stores.mugshots)
        self.census = Census(self.hosting, self.players)
        art = presence_art(settings.public_url)
        self.rich_presence = RichPresence(self.hosting, settings.discord_application_id, art)
        self.history = OnlineHistory(stores.history, self.census, games)
        self.feed = AdminFeed(stores.admin_log)
        self.catalogue_files = CatalogueFolder(settings.files_path) if settings.files_path else None
        self.catalogues = Catalogues(stores.catalogues, self.catalogue_files, games)
        self.figures = Figures(self.catalogues)
        self.store = Store(stores.products, self.catalogues, games)
        drops = Drops(self.catalogues, stores.entitlements)
        self.deals = HotDeals(stores.products, stores.deals, stores.settings, self.catalogues)
        self.purchases = Purchases(
            stores.products,
            stores.purchases,
            self.catalogues,
            self.heroes,
            stores.entitlements,
            drops,
            self.deals,
        )
        self.chests = Chests(stores.products, stores.purchases, self.heroes, drops)
        self.item_writes = ItemWrites(stores.entitlements)
        self.windows = StoreWindows(
            stores.products, self.catalogues, self.heroes, stores.entitlements, self.wallets
        )
        self.policies = GamePolicies(stores.settings)
        self.terms = Terms.load()
        self.store_items = StoreItems(
            stores.products, self.deals, self.catalogues, stores.entitlements, self.wallets
        )

    @asynccontextmanager
    async def running(self, app: FastAPI) -> AsyncIterator[None]:
        """Start the services and their followers once the database is ready, and stop them after.

        Args:
            app: The application, whose state hands the pages the launcher sign-in, the site's
                sign-in with its e-mailed proofs, its registration, the display names, the game
                password resets and the account management.
        """
        settings, games, stores = self._settings, self._games, self._stores
        routes, commands = self._routes, self._commands
        hosting, trust, players = self.hosting, self.trust, self.players
        feed, policies = self.feed, self.policies
        logins = Logins(stores.accounts)
        mailer = None
        if settings.smtp_host and settings.smtp_sender:
            mailer = SmtpMailer(settings, settings.smtp_host, settings.smtp_sender)
        outbox = Outbox(mailer, settings.public_url)
        verification = EmailVerification(stores.accounts, outbox)
        accounts = AccountService(
            stores.accounts,
            logins,
            stores.entitlements,
            stores.keys,
            self.terms,
            policies,
            verification,
            games,
        )
        personas = PersonaService(stores.personas)
        await personas.prepare(await accounts.prepare(games))
        app.state.launcher = LauncherService(
            logins, games, personas, self.heroes, self._launcher_sessions, self.start_links
        )
        mailings = Mailings()
        attempts = Attempts()
        sign_in = WebSignIn(
            stores.accounts,
            stores.identities,
            logins,
            policies,
            games,
            verification,
            attempts,
            mailings,
        )
        app.state.sign_in = sign_in
        app.state.proof_mail = ProofMail(
            stores.identities, stores.accounts, sign_in, outbox, mailings
        )
        app.state.resets = PasswordResets(
            stores.accounts, stores.accounts, logins, outbox, mailings, attempts
        )
        app.state.display_names = DisplayNames(stores.accounts, stores.personas, games)
        app.state.account_management = AccountManagement(
            stores.accounts,
            stores.identities,
            stores.personas,
            logins,
            attempts,
            outbox,
            games,
            [service.id for service in settings.sign_in_services],
            self.terms,
        )
        app.state.registration = WebRegistration(
            stores.accounts,
            stores.identities,
            logins,
            policies,
            games,
            verification,
            outbox,
            mailings,
            self.terms,
        )
        routes.update(connect_routes(games, policies))
        routes.update(telemetry_routes())
        routes.update(accounts.routes())
        routes.update(AccountSettingsService(stores.accounts, logins, policies).routes())
        routes.update(personas.routes())
        routes.update(EntitlementService(stores.entitlements, trust).routes())
        routes.update(KeyService(stores.keys).routes())
        routes.update(self.game_http.routes())
        own = OwnWrites(stores.stats, stores.entitlements, self.catalogues)
        routes.update(StatsService(stores.stats, stores.personas, trust, own).routes())
        routes.update(LeaderboardService(stores.stats).routes())
        routes.update(RecordService(stores.records).routes())
        routes.update(
            AssociationService(stores.associations, stores.personas, players, trust).routes()
        )
        routes.update(PresenceService(stores.personas, players, stores.associations).routes())
        messages = MessageService(stores.messages, stores.personas, players, stores.associations)
        routes.update(messages.routes())
        commands.update(theater_routes())
        commands.update(hosting.routes())
        commands.update(Browsing(hosting).routes())
        joining = Joining(hosting)
        commands.update(joining.routes())
        routes.update(PlayNow(hosting).routes())
        players.follow(feed)
        joining.follow(feed)
        self.catalogues.follow(feed)
        self.store.follow(feed)
        await feed.start()
        await self.rich_presence.follow_icons(stores.game_icons, feed)
        await self.catalogues.load()
        await self.store.stock()
        followers = [
            asyncio.create_task(trust.follow_approvals()),
            asyncio.create_task(feed.follow()),
            asyncio.create_task(self.history.follow()),
            asyncio.create_task(self.rich_presence.follow_changes()),
        ]
        yield
        for follower in followers:
            follower.cancel()
            with suppress(asyncio.CancelledError):
                await follower
        await messages.close()

    def start_session(
        self, websocket: WebSocket, hook_version: str | None, relay_token: str
    ) -> GameSession:
        """Create the core's session for a game that connected to the relay.

        Args:
            websocket: The accepted WebSocket that carries the game.
            hook_version: The version of the hook that opened it, when its User-Agent names one.
            relay_token: The secret token the WebSocket's upgrade was answered with.
        """
        address = self._settings.client_address(websocket)
        session = GameSession(
            self._routes,
            self.players,
            address,
            self._theater,
            hook_version,
            self.rich_presence,
            self._relay_tokens,
        )
        self._relay_tokens.add(relay_token, session)
        return session
