from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time
from math import ceil
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.games import Game, Games
from openfesl.core.hero_boards import HeroBoards, Placed, Ranking, Standings
from openfesl.core.stats import Bucket, bucket_of, partition_of
from openfesl.core.texts import localize
from openfesl.protocol.rank import Period
from openfesl.web.hero_stats import duration
from openfesl.web.pages import Pages, number

LEADERBOARDS = "/leaderboards"
PAGE_SIZE = 40
# The pages of a board, as on the 2012 site, which listed the best 1,000 heroes of each.
PAGES = 25
WEEKLY, HISTORIC = "weekly", "indefinite"
# The weeks that a weekly board looks back on, as the 2012 site's: this one and the three before.
WEEKS = 4


@dataclass(frozen=True, slots=True)
class Board:
    """A leaderboard of heroes as its address names it.

    Attributes:
        name: The stat it ranks by, the last part of its address.
        heroes: The faction or class whose heroes it lists; empty for every hero.
        period: Whether it ranks weeks or all time, as the address names them.
        past: How many weeks back, for a weekly board.
        page: The page, from 1.
    """

    name: str = "score"
    heroes: str = ""
    period: str = WEEKLY
    past: int = 0
    page: int = 1

    @property
    def bucket(self) -> Bucket:
        """The period whose stats it ranks."""
        # The buckets the game reads through FESL: weeks back by the periods past, or all time.
        return bucket_of(Period.WEEKLY if self.period == WEEKLY else Period.ALL_TIME, self.past)

    def changed(self, **changes: Any) -> Board:
        """Return the board with some of its choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return its address below the game's leaderboards."""
        listed = f"{self.heroes}-{self.name}" if self.heroes else self.name
        return f"/{listed}/{self.period}/{self.past}/{self.page}"


@dataclass(frozen=True, slots=True)
class Line:
    """A hero's line on a leaderboard, as the pages show it.

    Attributes:
        place: Its place, from 1.
        hero_id: The hero's persona.
        name: The hero's name.
        faction: Its faction, if the game knows it.
        hero_class: Its class, if the game knows it.
        level: Its level.
        account_id: The account it belongs to.
        player: The account's display name.
        value: What it is ranked by, as the page's language writes it.
    """

    place: int
    hero_id: int
    name: str
    faction: str | None
    hero_class: str | None
    level: int
    account_id: int
    player: str
    value: str


async def standings(boards: HeroBoards, game: Game, board: Board) -> Standings:
    """Return one of a game's leaderboards of heroes as last ranked.

    Args:
        boards: Rank the boards from the heroes' stats.
        game: The game, which has profile rules with the board.
        board: The board.
    """
    rules = game.heroes
    assert rules is not None and rules.profile is not None
    profile, stats = rules.profile, rules.stats
    within = None
    if faction := rules.factions.get(board.heroes):
        within = (0, faction.team)
    elif hero_class := rules.classes.get(board.heroes):
        within = (1, hero_class.kit)
    ranking = Ranking(
        keys=tuple(getattr(profile, name) for name in profile.boards[board.name]),
        time=profile.time,
        least=profile.listed_after,
        look=(stats.faction, stats.hero_class, stats.level),
        places=PAGES * PAGE_SIZE,
        within=within,
    )
    return await boards.standings(partition_of(game), game.namespace, ranking, board.bucket)


def lines(game: Game, board: Board, placed: Sequence[Placed], language: str) -> list[Line]:
    """Return heroes' lines of a leaderboard as the pages show them.

    Args:
        game: The game, which has profile rules with the board.
        board: The board.
        placed: The heroes' lines as ranked.
        language: The page's language.
    """
    rules = game.heroes
    assert rules is not None and rules.profile is not None
    names = rules.profile.boards[board.name]
    shown = []
    for line in placed:
        team, kit, level = line.look
        if len(names) == 2:
            value = number(line.value, language, 3)
        elif names == ["time"]:
            value = duration(line.value, language)
        else:
            value = number(line.value, language)
        shown.append(
            Line(
                place=line.place,
                hero_id=line.hero_id,
                name=line.name,
                faction=rules.faction_of(team),
                hero_class=rules.class_of(kit),
                level=max(level or 1, 1),
                account_id=line.account_id,
                player=line.player,
                value=value,
            )
        )
    return shown


def shares(game: Game, placed: Sequence[Placed]) -> list[tuple[str, int]]:
    """Return each faction's share of a board's heroes in percent, in the game's order.

    Args:
        game: The game, which has heroes.
        placed: The board's lines.
    """
    rules = game.heroes
    assert rules is not None
    counted = Counter(line.look[0] for line in placed if line.look[0] is not None)
    total = counted.total()
    return [
        (name, round(100 * counted[faction.team] / total))
        for name, faction in rules.factions.items()
        if total
    ]


class BoardPages:
    """The leaderboards of the heroes of games whose heroes have public profiles."""

    def __init__(self, pages: Pages, games: Games, boards: HeroBoards) -> None:
        """Prepare the leaderboards of each game that lists them.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the leaderboards get them.
            boards: Rank the boards from the heroes' stats.
        """
        self._pages = pages
        self._games = games
        self._boards = boards

    def routes(self) -> APIRouter:
        """Return the leaderboards of each game that lists them, and the form that picks one."""
        router = APIRouter()
        for game in self._games.with_page("leaderboards"):
            base = f"/{game.id}{LEADERBOARDS}"
            router.add_api_route(base, self._chosen(game))
            router.add_api_route(
                base + "/{listed}/{period}/{past:int}/{page:int}",
                self._page(game),
                response_class=HTMLResponse,
            )
        return router

    def _chosen(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def choose(request: Request) -> Response:
            # The menu's address and the form of the board's choices, which lead to the board's
            # own address; anything unknown keeps the first board's choice.
            query = request.query_params
            board = _read(game, query.get("board", "score"), query.get("heroes", ""))
            period = query.get("period", WEEKLY)
            board = board or Board()
            if period == HISTORIC:
                board = board.changed(period=HISTORIC)
            elif (past := query.get("past", "")).isdecimal() and int(past) < WEEKS:
                board = board.changed(past=int(past))
            path = localize(f"/{game.id}{LEADERBOARDS}{board.query()}", request.state.language)
            return RedirectResponse(path, 303)

        return choose

    def _page(self, game: Game) -> Callable[[Request, str, str, int, int], Awaitable[Response]]:
        rules = game.heroes
        assert rules is not None and rules.profile is not None
        profile = rules.profile

        async def show_board(
            request: Request, listed: str, period: str, past: int, page: int
        ) -> Response:
            # The faction or class comes first, as in royal-kills-deaths.
            heroes, _, name = listed.partition("-")
            board = _read(game, name, heroes) or _read(game, listed, "")
            weeks = range(WEEKS) if period == WEEKLY else range(1) if period == HISTORIC else ()
            if board is None or past not in weeks or page < 1:
                return self._pages.render(request, "Error", 404, game=game, code=404)
            board = board.changed(period=period, past=past)
            found = await standings(self._boards, game, board)
            # A page past the last shows the last.
            pages = max(ceil(len(found.lines) / PAGE_SIZE), 1)
            board = board.changed(page=min(page, pages))
            start = (board.page - 1) * PAGE_SIZE
            shown = found.lines[start : start + PAGE_SIZE]
            return self._pages.render(
                request,
                "heroes.Boards",
                game=game,
                board=board,
                boards=list(profile.boards),
                lines=lines(game, board, shown, request.state.language),
                total=len(found.lines),
                shares=shares(game, found.lines),
                pages=pages,
                first=start + 1,
                hours=profile.listed_after // 3600,
                places=PAGES * PAGE_SIZE,
                ranked=found.ranked,
                week=datetime.combine(board.bucket.start, time(), UTC),
                base=localize(f"/{game.id}{LEADERBOARDS}", request.state.language),
            )

        return show_board


def _read(game: Game, name: str, heroes: str) -> Board | None:
    # A board's stat and the faction or class it lists, when the game has them.
    rules = game.heroes
    assert rules is not None and rules.profile is not None
    known = heroes == "" or heroes in rules.factions or heroes in rules.classes
    return Board(name, heroes) if name in rules.profile.boards and known else None
