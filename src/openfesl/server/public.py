from fastapi import FastAPI, Request

from openfesl.core.games import Games
from openfesl.core.verification import Confirmation, EmailVerification
from openfesl.server.services import GameServices
from openfesl.server.settings import Settings
from openfesl.server.stores import Stores
from openfesl.web.account.proof import ProofPages
from openfesl.web.account.services import SignInServices
from openfesl.web.account.sign_in import SignInPages
from openfesl.web.activity import activity_routes
from openfesl.web.front import front_routes
from openfesl.web.game_icons import game_icon_routes
from openfesl.web.host import host_routes
from openfesl.web.launcher import launcher_routes
from openfesl.web.leaderboard import leaderboard_routes
from openfesl.web.menu import menu_routes
from openfesl.web.pages import Pages
from openfesl.web.play import play_routes, relay_address
from openfesl.web.players import player_routes
from openfesl.web.servers import server_routes
from openfesl.web.shell import shell_routes
from openfesl.web.verification import verification_routes


def include_public_routes(
    app: FastAPI,
    settings: Settings,
    games: Games,
    pages: Pages,
    services: GameServices,
    stores: Stores,
) -> None:
    """Add the public pages, the e-mail confirmation and the launcher's and the site's sign-in.

    Args:
        app: The application, whose state holds the services that the lifespan starts.
        settings: The deployment's settings.
        games: The games the server serves.
        pages: Renders the pages.
        services: The games' side of the server, which the pages show.
        stores: The database's stores.
    """
    census, history = services.census, services.history
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

    async def confirm(token: str) -> Confirmation:
        verification: EmailVerification = app.state.verification
        return await verification.confirm(token)

    app.include_router(verification_routes(confirm, pages))
    app.include_router(launcher_routes(lambda: app.state.launcher))
    turnstile = settings.turnstile
    sign_in_services = SignInServices(
        settings.sign_in_services, settings.public_url, lambda: app.state.sign_in
    )
    sign_in = SignInPages(
        pages,
        games,
        services.policies,
        lambda: app.state.sign_in,
        settings.sends_mail,
        turnstile,
        sign_in_services,
    )
    proof = ProofPages(pages, games, sign_in_services, lambda: app.state.sign_in, turnstile)
    app.include_router(sign_in.routes())
    app.include_router(sign_in_services.routes())
    app.include_router(proof.routes())
