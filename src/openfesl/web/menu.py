from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Activity, Census, Server
from openfesl.core.games import Game, Games
from openfesl.web.pages import Pages


@dataclass(frozen=True, slots=True)
class _Tile:
    game: Game
    activity: Activity
    busiest: Server | None


def menu_routes(pages: Pages, games: Games, census: Census) -> APIRouter:
    """Return OpenFESL's main menu, where a player picks a game.

    Args:
        pages: Renders the page.
        games: The games the menu offers.
        census: Counts each game's players and servers.
    """
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def show_menu(request: Request) -> HTMLResponse:
        tiles = [_Tile(game, census.activity(game), census.busiest(game)) for game in games]
        # The game with the most players online comes first.
        tiles.sort(key=lambda tile: -tile.activity.online)
        return pages.render(request, "Menu", steel=True, tiles=tiles)

    return router
