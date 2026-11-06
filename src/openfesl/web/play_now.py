from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.games import Game, Games
from openfesl.core.start_links import LIFETIME, LinkState, StartLink, StartLinks
from openfesl.web.account.session import PLAYER, signed_in
from openfesl.web.pages import Pages
from openfesl.web.site import LINK_STATUS, PLAY_NOW, site_bar

# How long the page waits for the launcher to take its link before it says what to do when
# nothing starts.
PATIENCE = timedelta(seconds=10)
# Each page holds a code that signs its account in, so no cache keeps one.
UNCACHED = {"Cache-Control": "no-store"}


class PlayNowPages:
    """The page that starts a game as the default hero through OpenFESLHook's launcher."""

    def __init__(self, pages: Pages, games: Games, links: StartLinks) -> None:
        """Prepare the Play Now page of each game whose launcher opens the site's start links.

        Args:
            pages: Renders the pages.
            games: The games, of which those whose site has start links get the page.
            links: Makes the start links and says where they stand.
        """
        self._pages = pages
        self._games = games
        self._links = links

    def routes(self) -> APIRouter:
        """Return each game's Play Now page and the state of its link, which the page asks for."""
        router = APIRouter()
        for game in self._games:
            if game.site and game.site.start_links:
                page, status = f"/{game.id}{PLAY_NOW}", f"/{game.id}{LINK_STATUS}"
                router.add_api_route(page, self._page(game), response_class=HTMLResponse)
                router.add_api_route(status, self._status(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_play_now(request: Request) -> Response:
            # Opening the page makes a new link, which ends the one an earlier visit made.
            player = signed_in(request)
            bar = await site_bar(request)
            hero = bar.roster.playing if bar and bar.roster else None
            link = None
            if player is None:
                state = "signed-out"
            elif hero is None:
                state = "no-hero"
            else:
                epoch = request.session[PLAYER]["epoch"]
                link = self._links.make(game.id, player.account_id, epoch, hero.persona.name)
                state = LinkState.WAITING.value
            return self._render(request, game, "heroes.PlayNow", state, link)

        return show_play_now

    def _status(self, game: Game) -> Callable[[Request, str], Awaitable[Response]]:
        async def show_status(request: Request, link: str = "") -> Response:
            player = signed_in(request)
            found = self._links.find(link, player.account_id) if player else None
            state = _state(found) if found else LinkState.ENDED.value
            return self._render(request, game, "heroes.PlayNowStatus", state, found)

        return show_status

    def _render(
        self, request: Request, game: Game, template: str, state: str, link: StartLink | None
    ) -> Response:
        return self._pages.render(
            request,
            template,
            game=game,
            headers=UNCACHED,
            state=state,
            link=link,
            minutes=int(LIFETIME / timedelta(minutes=1)),
        )


def _state(link: StartLink) -> str:
    # A link the launcher has not taken for a while waits on, said as a link that did not start.
    now = datetime.now(UTC)
    state = link.state(now)
    if state is LinkState.WAITING and now - link.made >= PATIENCE:
        return "missing"
    return state.value
