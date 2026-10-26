from collections.abc import Awaitable, Callable
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.entitlements import StoredKey
from openfesl.core.games import Game, Games
from openfesl.web.keys import KeyView, key_views
from openfesl.web.pages import Pages


class KeyList(Protocol):
    """Lists the product keys."""

    async def keys(self) -> list[StoredKey]:
        """Return every product key with what it grants, in the order they were added."""


async def game_keys(games: Games, store: KeyList, game: Game) -> list[KeyView]:
    """Return the enabled product keys for a game's players, starting keys first.

    Args:
        games: The games the server serves.
        store: Lists the product keys.
        game: The game.
    """
    return [
        view
        for view in key_views(games, await store.keys())
        if view.key.enabled and view.game is game
    ]


def play_key(game: Game, keys: list[KeyView]) -> KeyView | None:
    """Return the starting key that grants what the game's players need to play, when enabled.

    Args:
        game: The game.
        keys: Its enabled keys.
    """
    needed = game.play_key
    return next((view for view in keys if needed and view.key.code == needed.code), None)


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
    keys: KeyList,
    relay: Callable[[Request], str],
    hook_default: str,
) -> APIRouter:
    """Return each game's guide to setting the game up for this server.

    Args:
        pages: Renders the pages.
        games: The games, each with its guide under its id.
        census: Counts the players and servers.
        keys: Lists the product keys, of which the game's enabled ones are shown.
        relay: Returns this server's relay address for a request.
        hook_default: The relay address OpenFESLHook connects to without an OpenFESLHook.ini.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}/play",
            _guide(pages, games, census, keys, relay, hook_default, game),
            response_class=HTMLResponse,
        )
    return router


def _guide(
    pages: Pages,
    games: Games,
    census: Census,
    keys: KeyList,
    relay: Callable[[Request], str],
    hook_default: str,
    game: Game,
) -> Callable[[Request], Awaitable[HTMLResponse]]:
    async def show_guide(request: Request) -> HTMLResponse:
        address = relay(request)
        enabled = await game_keys(games, keys, game)
        return pages.render(
            request,
            "Play",
            game=game,
            activity=census.activity(game),
            # The hook needs its configuration only to reach another server than its default.
            relay=None if address == hook_default.rstrip("/") else address,
            keys=enabled,
            play_key=play_key(game, enabled),
        )

    return show_guide
