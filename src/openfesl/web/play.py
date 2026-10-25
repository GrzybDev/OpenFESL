from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.web.pages import Pages


def relay_address(request: Request, public_url: str | None) -> str:
    """Return the address of this server's relay WebSocket, which OpenFESLHook connects to.

    Args:
        request: A request to the server's web pages, which share the relay's host.
        public_url: The address the server's web pages are reached at, when the deployment names
            it; otherwise the request's own.
    """
    base = (public_url or str(request.base_url)).rstrip("/")
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def play_routes(
    pages: Pages,
    games: Games,
    census: Census,
    keys: EntitlementStore,
    relay: Callable[[Request], str],
    hook_default: str,
) -> APIRouter:
    """Return each game's guide to setting the game up for this server.

    Args:
        pages: Renders the pages.
        games: The games, each with its guide under its id.
        census: Counts the players and servers.
        keys: Tells which of the game's product keys are enabled.
        relay: Returns this server's relay address for a request.
        hook_default: The relay address OpenFESLHook connects to without an OpenFESLHook.ini.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}/play",
            _guide(pages, census, keys, relay, hook_default, game),
            response_class=HTMLResponse,
        )
    return router


def _guide(
    pages: Pages,
    census: Census,
    keys: EntitlementStore,
    relay: Callable[[Request], str],
    hook_default: str,
    game: Game,
) -> Callable[[Request], Awaitable[HTMLResponse]]:
    async def show_guide(request: Request) -> HTMLResponse:
        address = relay(request)
        enabled = [key for key in game.keys if await keys.grants(key.code) is not None]
        play_key = game.play_key
        return pages.render(
            request,
            "Play",
            game=game,
            activity=census.activity(game),
            # The hook needs its configuration only to reach another server than its default.
            relay=None if address == hook_default.rstrip("/") else address,
            keys=enabled,
            play_key=play_key if play_key in enabled else None,
        )

    return show_guide
