from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.games import Game, Games
from openfesl.core.servers import Approval, HostingMode
from openfesl.web.pages import Pages


def host_routes(
    pages: Pages,
    games: Games,
    census: Census,
    approval: Approval,
    contact: str | None,
    relay: Callable[[Request], str],
    hook_default: str,
) -> APIRouter:
    """Return each game's guide to hosting a dedicated server on this server.

    Args:
        pages: Renders the pages.
        games: The games whose players may host, each with its guide under its id.
        census: Counts the players and servers.
        approval: Knows whether new servers wait for the administrators' approval.
        contact: The address at which operators reach the administrators, if the deployment
            names one.
        relay: Returns this server's relay address for a request.
        hook_default: The relay address OpenFESLHook connects to without an OpenFESLHook.ini.
    """
    router = APIRouter()
    for game in games:
        if game.server_files:
            router.add_api_route(
                f"/{game.id}/host",
                _guide(pages, census, approval, contact, relay, hook_default, game),
                response_class=HTMLResponse,
            )
    return router


def _guide(
    pages: Pages,
    census: Census,
    approval: Approval,
    contact: str | None,
    relay: Callable[[Request], str],
    hook_default: str,
    game: Game,
) -> Callable[[Request], Awaitable[HTMLResponse]]:
    async def show_guide(request: Request) -> HTMLResponse:
        address = relay(request)
        return pages.render(
            request,
            "Host",
            game=game,
            activity=census.activity(game),
            files=game.server_files,
            approved_only=await approval.mode() is HostingMode.APPROVED,
            contact=contact,
            relay=None if address == hook_default.rstrip("/") else address,
        )

    return show_guide
