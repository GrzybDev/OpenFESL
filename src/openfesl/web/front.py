from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.games import Game, Games
from openfesl.core.history import OnlineHistory
from openfesl.web.pages import Pages
from openfesl.web.play import KeyList, game_keys, play_key


def front_routes(
    pages: Pages, games: Games, census: Census, history: OnlineHistory, keys: KeyList
) -> APIRouter:
    """Return each game's front page: who plays it now, where to join, and how to set it up.

    Args:
        pages: Renders the pages.
        games: The games, each with a front page at its id.
        census: Counts each game's players and servers.
        history: Holds each game's peak today and its record.
        keys: Lists the product keys, of which the one that lets players in is named.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}",
            _front(pages, games, census, history, keys, game),
            response_class=HTMLResponse,
        )
    return router


def _front(
    pages: Pages,
    games: Games,
    census: Census,
    history: OnlineHistory,
    keys: KeyList,
    game: Game,
) -> Callable[[Request], Awaitable[HTMLResponse]]:
    async def show_front(request: Request) -> HTMLResponse:
        needed = play_key(game, await game_keys(games, keys, game))
        return pages.render(
            request,
            "Front",
            game=game,
            activity=census.activity(game),
            busiest=census.busiest(game),
            overview=await history.overview(game, datetime.now(UTC)),
            play_key=needed.key.code if needed else None,
        )

    return show_front
