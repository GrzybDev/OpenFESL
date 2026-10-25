from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.games import Game, Games
from openfesl.web.pages import Pages


def front_routes(pages: Pages, games: Games, census: Census) -> APIRouter:
    """Return each game's front page: who plays it now, where to join, and how to set it up.

    Args:
        pages: Renders the pages.
        games: The games, each with a front page at its id.
        census: Counts each game's players and servers.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}", _front(pages, census, game), response_class=HTMLResponse
        )
    return router


def _front(
    pages: Pages, census: Census, game: Game
) -> Callable[[Request], Awaitable[HTMLResponse]]:
    async def show_front(request: Request) -> HTMLResponse:
        return pages.render(
            request,
            "Front",
            game=game,
            activity=census.activity(game),
            busiest=census.busiest(game),
        )

    return show_front
