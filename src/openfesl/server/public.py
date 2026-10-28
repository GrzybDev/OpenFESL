from fastapi import FastAPI, Request

from openfesl.core.games import Games
from openfesl.server.services import GameServices
from openfesl.server.settings import Settings
from openfesl.server.stores import Stores
from openfesl.web.account.deletion import DeletionPages
from openfesl.web.account.display_name import DisplayNamePages
from openfesl.web.account.game_password import GamePasswordForms
from openfesl.web.account.page import AccountPages
from openfesl.web.account.proof import ProofPages
from openfesl.web.account.register import RegisterPages
from openfesl.web.account.reset import ResetPages
from openfesl.web.account.services import SignInServices
from openfesl.web.account.sign_in import SignInPages
from openfesl.web.account.terms import TermsPages
from openfesl.web.activity import activity_routes
from openfesl.web.chest_keys import KeyPages
from openfesl.web.chests import ChestsPages
from openfesl.web.creator import CreatorPages
from openfesl.web.front import front_routes
from openfesl.web.funds import FundsPages
from openfesl.web.game_icons import game_icon_routes
from openfesl.web.heroes import HeroesPages
from openfesl.web.host import host_routes
from openfesl.web.launcher import launcher_routes
from openfesl.web.leaderboard import leaderboard_routes
from openfesl.web.menu import menu_routes
from openfesl.web.open import open_routes
from openfesl.web.pages import Pages
from openfesl.web.play import play_routes, relay_address
from openfesl.web.play_now import PlayNowPages
from openfesl.web.players import player_routes
from openfesl.web.servers import server_routes
from openfesl.web.sessions import SentForms
from openfesl.web.shell import shell_routes
from openfesl.web.store import StorePages


def include_public_routes(
    app: FastAPI,
    settings: Settings,
    games: Games,
    pages: Pages,
    services: GameServices,
    stores: Stores,
) -> None:
    """Add the public pages, the launcher's sign-in and the site's accounts.

    Args:
        app: The application, whose state holds the services that the lifespan starts.
        settings: The deployment's settings.
        games: The games the server serves.
        pages: Renders the pages.
        services: The games' side of the server, which the pages show.
        stores: The database's stores.
    """
    census, history = services.census, services.history
    sent = SentForms()
    app.include_router(game_icon_routes(games, stores.game_icons))
    app.include_router(shell_routes(pages, services.terms))
    app.include_router(menu_routes(pages, games, census))
    app.include_router(front_routes(pages, games, census, history, stores.keys))
    app.include_router(activity_routes(pages, games, census, history))
    app.include_router(server_routes(pages, games, census))
    app.include_router(leaderboard_routes(pages, games, census, stores.stats))
    app.include_router(
        player_routes(pages, games, census, stores.stats, stores.personas, stores.records)
    )

    def relay(request: Request) -> str:
        return relay_address(request, settings.public_url)

    app.include_router(
        play_routes(pages, games, census, stores.keys, relay, settings.hook_default_url)
    )
    app.include_router(
        host_routes(
            pages,
            games,
            census,
            services.approval,
            settings.contact_email,
            relay,
            settings.hook_default_url,
        )
    )

    app.include_router(CreatorPages(pages, games, services.heroes, services.catalogues).routes())
    app.include_router(
        HeroesPages(
            pages,
            games,
            services.heroes,
            sent,
            services.wallets,
            stores.entitlements,
            services.catalogues,
            services.chests,
        ).routes()
    )
    app.include_router(
        ChestsPages(
            pages,
            games,
            services.chests,
            stores.entitlements,
            services.catalogues,
            services.wallets,
        ).routes()
    )
    app.include_router(
        KeyPages(
            pages,
            games,
            services.chests,
            sent,
            services.purchases,
            stores.entitlements,
            services.wallets,
        ).routes()
    )
    app.include_router(open_routes(games))
    app.include_router(PlayNowPages(pages, games, services.start_links).routes())
    app.include_router(
        StorePages(
            pages, games, services.store_items, sent, services.purchases, services.catalogues
        ).routes()
    )
    app.include_router(FundsPages(pages, games, services.wallets).routes())
    app.include_router(launcher_routes(lambda: app.state.launcher))
    turnstile = settings.turnstile
    sign_in_services = SignInServices(
        settings.sign_in_services,
        settings.public_url,
        lambda: app.state.sign_in,
        stores.accounts.account,
        lambda: app.state.account_management,
        services.terms,
    )
    sign_in = SignInPages(
        pages,
        games,
        services.policies,
        lambda: app.state.sign_in,
        settings.sends_mail,
        turnstile,
        sign_in_services,
        services.terms,
    )
    proof = ProofPages(
        pages,
        games,
        sign_in_services,
        lambda: app.state.sign_in,
        lambda: app.state.proof_mail,
        settings.sends_mail,
        turnstile,
        services.terms,
    )
    register = RegisterPages(
        pages,
        games,
        services.policies,
        sign_in_services,
        lambda: app.state.registration,
        settings.sends_mail,
        turnstile,
    )
    app.include_router(sign_in.routes())
    app.include_router(sign_in_services.routes())
    app.include_router(proof.routes())
    app.include_router(register.routes())
    display_name = DisplayNamePages(
        pages,
        games,
        stores.accounts.account,
        lambda: app.state.display_names,
        services.terms,
        sign_in_services,
    )
    app.include_router(display_name.routes())
    account = AccountPages(
        pages,
        games,
        stores.accounts.account,
        lambda: app.state.account_management,
        lambda: app.state.sign_in,
        sign_in_services,
        services.policies,
        stores.stats,
        services.terms,
        settings.sends_mail,
        turnstile,
    )
    app.include_router(account.routes())
    terms = TermsPages(
        pages, stores.accounts.account, lambda: app.state.account_management, services.terms
    )
    app.include_router(terms.routes())
    game_password = GamePasswordForms(
        pages,
        games,
        stores.accounts.account,
        lambda: app.state.account_management,
        sign_in_services,
    )
    app.include_router(game_password.routes())
    deletion = DeletionPages(
        pages,
        stores.accounts.account,
        lambda: app.state.account_management,
        sign_in_services,
        services.feed.apply,
    )
    app.include_router(deletion.routes())
    resets = ResetPages(
        pages,
        games,
        lambda: app.state.resets,
        settings.sends_mail,
        turnstile,
        bool(settings.sign_in_services),
    )
    app.include_router(resets.routes())
