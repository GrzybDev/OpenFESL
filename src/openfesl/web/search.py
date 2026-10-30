from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from math import ceil
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero, Heroes, Roster
from openfesl.core.relationships import (
    Found,
    Party,
    Refusal,
    Relationship,
    RelationshipRefused,
    Relationships,
)
from openfesl.core.texts import localize
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_token

SEARCH = "/search"
# How many heroes, and how many players, a page of the search lists.
PAGE_SIZE = 40
# The search's lists, each paged by the address's parameter of its name.
HEROES, PLAYERS = "heroes", "players"


@dataclass(frozen=True, slots=True)
class Result:
    """A hero or a player that a search found, as the search page lists it.

    Attributes:
        name: The name that matched.
        member: Its account, as a side of a friendship.
        player: The account's display name.
        hero: The hero the name is of; none for a player found by display name, or a hero
            renamed since.
        heroes: How many heroes in play the account has, for a player.
        default: The account's default hero, while in play, for a player.
        relationship: How the signed-in account relates to it; none when it does not.
    """

    name: str
    member: Party
    player: str
    hero: Hero | None
    heroes: int
    default: Hero | None
    relationship: Relationship | None


@dataclass(frozen=True, slots=True)
class Listed:
    """One list of what a search found, at the page its address asks for.

    Attributes:
        kind: Which list it is: heroes or players.
        results: Its results on the page.
        total: How many it found.
        q: The search.
        at: The page that each of the search's lists shows, from 1, by its kind.
    """

    kind: str
    results: list[Result]
    total: int
    q: str
    at: dict[str, int]

    @property
    def page(self) -> int:
        """The page it shows, from 1."""
        return self.at[self.kind]

    @property
    def pages(self) -> int:
        """How many pages it fills."""
        return max(ceil(self.total / PAGE_SIZE), 1)

    @property
    def first(self) -> int:
        """The place in the list of its page's first result, from 1."""
        return (self.page - 1) * PAGE_SIZE + 1

    def changed(self, page: int) -> Listed:
        """Return the list at another page, for the pager's links.

        Args:
            page: The page.
        """
        return replace(self, at=self.at | {self.kind: page})

    def query(self) -> str:
        """Return the query of the search's address at this list's page."""
        return "?" + urlencode({"q": self.q} | {k: page for k, page in self.at.items() if page > 1})


class SearchPages:
    """The player search of a game's site: heroes and players by the start of their name."""

    def __init__(
        self, pages: Pages, games: Games, relationships: Relationships, heroes: Heroes
    ) -> None:
        """Prepare the player search of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            relationships: Finds the players and says how the account relates to each.
            heroes: Tell each found account's heroes.
        """
        self._pages = pages
        self._games = games
        self._relationships = relationships
        self._heroes = heroes

    def routes(self) -> APIRouter:
        """Return the player search of each game that lists it."""
        router = APIRouter()
        for game in self._games.with_page("search"):
            path = f"/{game.id}{SEARCH}"
            router.add_api_route(path, self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def search(request: Request) -> Response:
            player = signed_in(request)
            query = request.query_params.get("q", "").strip()
            at = {
                kind: max(int(page), 1)
                if (page := request.query_params.get(kind, "")).isdigit()
                else 1
                for kind in (HEROES, PLAYERS)
            }
            listed: list[Listed] = []
            short = False
            if query:
                try:
                    found = await self._relationships.matches(
                        game,
                        query,
                        *(slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE) for page in at.values()),
                    )
                except RelationshipRefused as refused:
                    assert refused.refusal is Refusal.SEARCH_TOO_SHORT
                    short = True
                else:
                    me = Party(player.account_id) if player else None
                    mine = await self._relationships.roster(game, me) if me else []
                    known = {r.member.account_id: r for r in mine if r.member.account_id}
                    # Each account's heroes are read once, however many of its names matched.
                    rosters: dict[int, Roster] = {}
                    lists = (
                        (HEROES, found.heroes, found.hero_count),
                        (PLAYERS, found.players, found.player_count),
                    )
                    for kind, each, total in lists:
                        results = [await self._result(game, one, known, rosters) for one in each]
                        listed.append(Listed(kind, results, total, query, at))
            return self._pages.render(
                request,
                "friends.Search",
                game=game,
                query=query,
                short=short,
                listed=listed,
                address=localize(f"/{game.id}{SEARCH}", request.state.language),
                self_id=player.account_id if player else None,
                csrf=form_token(request) if player else "",
            )

        return search

    async def _result(
        self, game: Game, found: Found, known: dict[int, Relationship], rosters: dict[int, Roster]
    ) -> Result:
        account_id = found.account_id
        if account_id not in rosters:
            rosters[account_id] = await self._heroes.roster(game, account_id)
        roster = rosters[account_id]
        # Heroes the admin took out of play are not shown to other accounts.
        heroes = [hero for hero in roster.heroes if not hero.disabled]
        default = roster.default if roster.default in heroes else None
        named = next((h for h in heroes if h.persona.name == found.name), None)
        return Result(
            found.name,
            Party(account_id),
            found.player,
            named if found.hero else None,
            len(heroes),
            default,
            known.get(account_id),
        )
