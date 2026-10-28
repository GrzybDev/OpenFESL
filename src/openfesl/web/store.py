from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.games import Game, Games
from openfesl.core.heroes import Roster
from openfesl.core.store_items import HeroChoice, PricePoint, StoreItem, StoreItems
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages
from openfesl.web.site import site_bar
from openfesl.web.store_quote import QuoteTexts, quote

ITEM = "/store/buy/{number}"


class StorePages:
    """The pages of a game's store on its site: what each sells, and to which of the heroes."""

    def __init__(self, pages: Pages, games: Games, items: StoreItems) -> None:
        """Prepare the store pages of each game whose heroes buy on its site.

        Args:
            pages: Renders the pages.
            games: The games, of which those with a store and heroes get the pages.
            items: Finds what a page sells and what each hero may buy of it.
        """
        self._pages = pages
        self._games = games
        self._items = items

    def routes(self) -> APIRouter:
        """Return each game's store pages."""
        router = APIRouter()
        for game in self._games:
            if game.store and game.heroes:
                page = f"/{game.id}{ITEM}"
                router.add_api_route(page, self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request, str], Awaitable[Response]]:
        async def show_item(request: Request, number: str) -> Response:
            chosen = request.query_params
            language = request.state.language
            sold = await self._items.find(game, _number(number), language)
            if sold is None:
                raise HTTPException(404)
            bar = await site_bar(request)
            roster = bar.roster if bar else None
            player = signed_in(request)
            choices = (
                await self._items.choices(game, sold, player.account_id, roster.heroes)
                if player and roster and roster.heroes
                else []
            )
            picked = _chosen(choices, roster, chosen.get("hero"))
            offered = None
            if picked is not None:
                point = _price(sold, chosen.get("price")) or _price(sold, number) or sold.prices[0]
                offered = quote(sold, picked, point, datetime.now(UTC))
            return self._pages.render(
                request,
                "store.StoreItem",
                game=game,
                sold=sold,
                roster=roster,
                choices=choices,
                offered=offered,
                texts=QuoteTexts(game, language, datetime.now(UTC)),
            )

        return show_item


def _number(text: str) -> int:
    # A number the database holds, of at most 18 digits; anything else finds no page.
    if not (text.isascii() and text.isdecimal() and len(text) <= 18):
        raise HTTPException(404)
    return int(text)


def _price(sold: StoreItem, chosen: str | None) -> PricePoint | None:
    return next((price for price in sold.prices if str(price.number) == chosen), None)


def _chosen(
    choices: list[HeroChoice], roster: Roster | None, hero: str | None
) -> HeroChoice | None:
    # The hero asked for, else the default hero when it can buy, else the first that can.
    asked = next((c for c in choices if str(c.hero.persona.id) == hero), None)
    if asked or not choices:
        return asked
    default = roster.default_id if roster else None
    able = [c for c in choices if c.unfit is None and not c.owned]
    preferred = [c for c in able if c.hero.persona.id == default] + able
    fallback = [c for c in choices if c.hero.persona.id == default] + choices
    return (preferred or fallback)[0]
