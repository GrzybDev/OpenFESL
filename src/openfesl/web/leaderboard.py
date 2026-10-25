from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from math import ceil
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams

from openfesl.core.census import Census
from openfesl.core.games import Game, Games
from openfesl.core.rankings import KD, RankingStore
from openfesl.core.stats import bucket_of, partition_of
from openfesl.core.texts import localize
from openfesl.protocol.rank import Period
from openfesl.web.pages import Pages

PAGE_SIZE = 50
# What the leaderboard sorts by, in the order of its tabs.
ORDERS = ("score", "kills", KD, "dog_tags", "time")


@dataclass(frozen=True, slots=True)
class View:
    """How a reader looks at a game's leaderboard, as its address says.

    Attributes:
        order: The stat the players are sorted by.
        online: Whether only the players online now are listed, with their overall positions.
        player: The name, or part of it, of the player the reader looks for.
        page: The page of the list, from 1.
    """

    order: str = "score"
    online: bool = False
    player: str = ""
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the view of a leaderboard's address; whatever it does not name stays as it was.

        Args:
            query: The address's query.
        """
        order = query.get("stat", "")
        page = query.get("page", "")
        return cls(
            order=order if order in ORDERS else "score",
            online=query.get("show") == "online",
            player=query.get("player", "").strip(),
            page=int(page) if page.isdecimal() and int(page) > 0 else 1,
        )

    def changed(self, **changes: Any) -> View:
        """Return the view with some choices changed, from the first page.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **({"page": 1} | changes))

    def query(self, *, paged: bool = False) -> str:
        """Return the query of the leaderboard's address, with a leading ? unless it is empty.

        Args:
            paged: Whether the query names the first page too, as one that a search chose.
        """
        fields = [
            ("stat", "" if self.order == "score" else self.order),
            ("show", "online" if self.online else ""),
            ("player", self.player),
            ("page", "" if self.page == 1 and not paged else str(self.page)),
        ]
        query = urlencode([(key, value) for key, value in fields if value])
        return f"?{query}" if query else ""


def leaderboard_routes(
    pages: Pages, games: Games, census: Census, store: RankingStore
) -> APIRouter:
    """Return each game's leaderboard of its players' stats.

    Args:
        pages: Renders the pages.
        games: The games, each with its leaderboard under its id.
        census: Knows who is online and on which server.
        store: Sums up and orders the players' stats.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}/leaderboard",
            _board(pages, census, store, game),
            response_class=HTMLResponse,
        )
    return router


def _board(
    pages: Pages, census: Census, store: RankingStore, game: Game
) -> Callable[[Request], Awaitable[Response]]:
    partition = partition_of(game)
    sums = game.stats.sums()
    keys = sorted({key for keys in sums.values() for key in keys})

    async def show_board(request: Request) -> Response:
        view = View.read(request.query_params)
        bucket = bucket_of(Period.ALL_TIME, 0)
        places = census.places(game)
        among = list(places) if view.online else None
        everyone = await store.count(partition, keys, bucket, None)
        online = await store.count(partition, keys, bucket, list(places))
        listed = online if view.online else everyone
        found = None
        if view.player:
            found = await store.find(partition, sums, view.order, bucket, view.player)
        position = None
        if found and not view.online:
            position = found.place
        elif found and found.persona_id in places:
            # Among the players online, positions on the pages skip those offline.
            ahead = await store.lines(
                partition, sums, view.order, bucket, among, slice(0, found.place)
            )
            position = next(
                index for index, line in enumerate(ahead, 1) if line.persona_id == found.persona_id
            )
        if found and position and "page" not in request.query_params:
            # The search opens the page that holds the player, at the player's line.
            target = replace(view, page=ceil(position / PAGE_SIZE)).query(paged=True)
            path = localize(request.url.path, request.state.language)
            return RedirectResponse(f"{path}{target}#persona-{found.persona_id}", 303)
        pages_count = max(ceil(listed / PAGE_SIZE), 1)
        page = min(view.page, pages_count)
        start = (page - 1) * PAGE_SIZE
        lines = await store.lines(
            partition, sums, view.order, bucket, among, slice(start, start + PAGE_SIZE)
        )
        return pages.render(
            request,
            "Leaderboard",
            game=game,
            activity=census.activity(game),
            view=replace(view, page=page),
            orders=ORDERS,
            lines=lines,
            places=places,
            everyone=everyone,
            online=online,
            listed=listed,
            first=start + 1,
            pages=pages_count,
            found=found,
            found_page=ceil(position / PAGE_SIZE) if position else None,
        )

    return show_board
