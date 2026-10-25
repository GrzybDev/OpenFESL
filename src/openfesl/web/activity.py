import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.games import Game, Games
from openfesl.core.history import Chart, OnlineHistory, Period
from openfesl.web.pages import Pages

# Room above the highest bar for its number.
HEADROOM = 1.12


@dataclass(frozen=True, slots=True)
class Scale:
    """The activity chart's vertical scale.

    Attributes:
        top: The number of players at the top of the chart.
        lines: The numbers the chart draws a line at, between none and the top.
    """

    top: int
    lines: tuple[int, ...]

    def height(self, players: int) -> int:
        """Return how high a number of players reaches, in whole percents of the chart.

        Args:
            players: The number of players.
        """
        return min(round(players * 100 / self.top), 100)


def scale(chart: Chart) -> Scale:
    """Return the scale of a chart, with room for its highest bar and its record.

    The lines are a round step apart, 1, 2 or 5 times a power of ten, so that at most five fit.

    Args:
        chart: The chart.
    """
    shown = [bar.peak.online for bar in chart.bars if bar.peak]
    if chart.record:
        shown.append(chart.record.online)
    top = max(math.ceil(max(shown, default=0) * HEADROOM), 1)
    step = next(
        size * 10**power
        for power in range(len(str(top)))
        for size in (1, 2, 5)
        if top / (size * 10**power) <= 5
    )
    return Scale(top, tuple(range(step, top, step)))


def activity_routes(
    pages: Pages, games: Games, census: Census, history: OnlineHistory
) -> APIRouter:
    """Return each game's activity page: its players online by hour, day or week, with the peaks.

    Args:
        pages: Renders the pages.
        games: The games, each with an activity page.
        census: Counts each game's players now.
        history: Holds the peaks of each game's players online.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}/activity",
            _activity(pages, census, history, game),
            response_class=HTMLResponse,
        )
    return router


def _activity(
    pages: Pages, census: Census, history: OnlineHistory, game: Game
) -> Callable[[Request, str], Awaitable[HTMLResponse]]:
    async def show_activity(request: Request, period: str = Period.DAY) -> HTMLResponse:
        now = datetime.now(UTC)
        chosen = Period(period) if period in Period else Period.DAY
        chart = await history.chart(game, chosen, now)
        return pages.render(
            request,
            "Activity",
            game=game,
            activity=census.activity(game),
            overview=await history.overview(game, now),
            chart=chart,
            scale=scale(chart),
            week=await history.chart(game, Period.WEEK, now),
            periods=list(Period),
        )

    return show_activity
