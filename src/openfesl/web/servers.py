from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from math import ceil
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import QueryParams

from openfesl.core.census import Census, Label, Server
from openfesl.core.games import Game, Games
from openfesl.core.texts import translate
from openfesl.web.pages import Pages

PAGE_SIZE = 50
SORTS = ("players", "name", "map", "mode", "region")
# The states a reader may show only, of which a server needs one, and the hardcore rules.
STATES = ("squad", "full", "empty")
SHOWN = (*STATES, "hardcore")


@dataclass(frozen=True, slots=True)
class Filters:
    """What a reader narrowed a game's server list to and how it is sorted, as its address says.

    Attributes:
        name: Part of a server's name.
        mode: The name of the game mode the servers play.
        map: The name of the map they play on.
        region: The region they are in, by its name or as the servers publish it.
        show: The server states and rules shown only.
        sort: The column the list is sorted by.
        descending: Whether the list runs from the highest value down.
        page: The page of the list, from 1.
        open: The server whose details are open, by the end of its address.
    """

    name: str = ""
    mode: str | None = None
    map: str | None = None
    region: str | None = None
    show: frozenset[str] = field(default_factory=frozenset)
    sort: str = "players"
    descending: bool = True
    page: int = 1
    open: str | None = None

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the filters of a list's address; whatever it does not name stays as it was.

        A form sends the button it was sent with last, after the hidden fields that keep the
        list's other choices, so the last value of a field counts.

        Args:
            query: The address's query.
        """

        def last(key: str) -> str:
            values = query.getlist(key)
            return values[-1].strip() if values else ""

        sort = last("sort") if last("sort") in SORTS else "players"
        order = last("order")
        page = last("page")
        return cls(
            name=last("q"),
            mode=last("mode") or None,
            map=last("map") or None,
            region=last("region") or None,
            show=frozenset(state for state in SHOWN if last(state) == "1"),
            sort=sort,
            descending=order == "desc" if order in ("asc", "desc") else sort == "players",
            page=int(page) if page.isdecimal() and int(page) > 0 else 1,
            open=last("open") or None,
        )

    @property
    def narrowed(self) -> bool:
        """Whether any filter leaves servers out."""
        return bool(self.name or self.mode or self.map or self.region or self.show)

    def admits(self, server: Server) -> bool:
        """Return whether a server passes the filters.

        Args:
            server: The server.
        """
        states = {
            "squad": server.room,
            "full": server.free == 0,
            "empty": server.players == 0,
        }
        wanted = self.show & set(STATES)
        return (
            self.name.casefold() in server.name.casefold()
            and self.mode in (None, _key(server.mode))
            and self.map in (None, _key(server.map))
            and self.region in (None, _key(server.region))
            and (not wanted or any(states[state] for state in wanted))
            and ("hardcore" not in self.show or server.hardcore)
        )

    def changed(self, **changes: Any) -> Filters:
        """Return the filters with some changed, from the first page and with no details open.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **({"page": 1, "open": None} | changes))

    def without(self, shown: str) -> Filters:
        """Return the filters without one of the states or rules shown only.

        Args:
            shown: The state or rule.
        """
        return self.changed(show=self.show - {shown})

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        fields = [
            ("q", self.name),
            ("mode", self.mode or ""),
            ("map", self.map or ""),
            ("region", self.region or ""),
            *[(state, "1") for state in SHOWN if state in self.show],
            ("sort", "" if self.sort == "players" else self.sort),
            ("order", "" if self.descending == (self.sort == "players") else self.direction),
            ("page", "" if self.page == 1 else str(self.page)),
            ("open", self.open or ""),
        ]
        query = urlencode([(key, value) for key, value in fields if value])
        return f"?{query}" if query else ""

    @property
    def direction(self) -> str:
        """The sort order as the address names it."""
        return "desc" if self.descending else "asc"


def server_routes(pages: Pages, games: Games, census: Census) -> APIRouter:
    """Return each game's server list.

    Args:
        pages: Renders the pages.
        games: The games, each with its list under its id.
        census: Knows the servers each game's hosts list.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(
            f"/{game.id}/servers", _list(pages, census, game), response_class=HTMLResponse
        )
    return router


def _list(pages: Pages, census: Census, game: Game) -> Callable[[Request], Awaitable[HTMLResponse]]:
    async def show_servers(request: Request) -> HTMLResponse:
        filters = Filters.read(request.query_params)
        language: str = request.state.language
        every = census.servers(game)
        found = sorted(
            (server for server in every if filters.admits(server)),
            key=lambda server: server.players,
            reverse=True,
        )
        if filters.sort != "players":
            # A stable sort keeps the most players first among equal values.
            found.sort(
                key=lambda server: _sort_key(game, filters.sort, server, language),
                reverse=filters.descending,
            )
        elif not filters.descending:
            found.reverse()
        pages_count = max(ceil(len(found) / PAGE_SIZE), 1)
        page = min(filters.page, pages_count)
        regions = Counter(_key(server.region) for server in every if server.region)
        labels = {_key(server.region): server.region for server in every if server.region}
        return pages.render(
            request,
            "Servers",
            game=game,
            activity=census.activity(game),
            filters=replace(filters, page=page),
            servers=found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
            found=len(found),
            total=len(every),
            first=(page - 1) * PAGE_SIZE + 1,
            pages=pages_count,
            modes=Counter(_key(server.mode) for server in every if server.mode),
            regions=sorted(
                ((labels[key], count) for key, count in regions.items() if key),
                key=lambda region: _name(game, "region", region[0], language).casefold(),
            ),
            shown=Counter(
                state
                for server in every
                for state, passes in (
                    ("squad", server.room),
                    ("full", server.free == 0),
                    ("empty", server.players == 0),
                    ("hardcore", server.hardcore),
                )
                if passes
            ),
        )

    return show_servers


def _key(label: Label | None) -> str | None:
    # A value the game data names is known by its name; any other by itself.
    return (label.name or label.value) if label else None


def _name(game: Game, kind: str, label: Label | None, language: str) -> str:
    if label is None:
        return ""
    if label.name:
        return translate(f"games.{game.id}.{kind}.{label.name}", language)
    return label.value


def _sort_key(game: Game, column: str, server: Server, language: str) -> str:
    if column == "name":
        return server.name.casefold()
    label = {"map": server.map, "mode": server.mode, "region": server.region}[column]
    return _name(game, column, label, language).casefold()
