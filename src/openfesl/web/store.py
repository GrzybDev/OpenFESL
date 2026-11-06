from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogues
from openfesl.core.game_pages import PageKind
from openfesl.core.games import Game, Games, Scope
from openfesl.core.heroes import Roster
from openfesl.core.purchases import (
    ALREADY_OWNED,
    INSUFFICIENT_FUNDS,
    LEVEL_MISSING,
    NOT_FOR_HERO,
    PurchaseRefused,
    Purchases,
)
from openfesl.core.store import Product
from openfesl.core.store_items import HeroChoice, Kind, PricePoint, StoreItem, StoreItems, Unfit
from openfesl.core.texts import localize, translate
from openfesl.web.account.session import open_page, signed_in
from openfesl.web.catalogue_files import icon_url
from openfesl.web.pages import Pages
from openfesl.web.sessions import (
    SentForms,
    form_matches,
    form_token,
    or_404,
    purchase_matches,
    toast,
    toast_at,
)
from openfesl.web.site import site_bar
from openfesl.web.store_quote import Quote, QuoteTexts, quote

ITEM = "/store/buy/{number}"
# The box a purchase opened, which the next page of the store shows once.
REVEAL = "store_reveal"


class StorePages:
    """The pages of a game's store on its site: what each sells, and buying it for a hero."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        items: StoreItems,
        sent: SentForms,
        purchases: Purchases,
        catalogues: Catalogues,
    ) -> None:
        """Prepare the store pages of each game whose heroes buy on its site.

        Args:
            pages: Renders the pages.
            games: The games, of which those with a store and heroes get the pages.
            items: Finds what a page sells and what each hero may buy of it.
            sent: Lets each confirmation buy once.
            purchases: Sells it, as the game's own store does.
            catalogues: Name what a box gave in the page's language, and find the items' icons.
        """
        self._pages = pages
        self._games = games
        self._items = items
        self._sent = sent
        self._purchases = purchases
        self._catalogues = catalogues

    def routes(self) -> APIRouter:
        """Return each game's store pages, their review of a choice and their purchase."""
        router = APIRouter()
        for game in self._games:
            if game.store and game.heroes:
                page = f"/{game.id}{ITEM}"
                router.add_api_route(page, self._page(game), response_class=HTMLResponse)
                router.add_api_route(page, self._review(game), methods=["POST"])
                router.add_api_route(page + "/confirm", self._buy(game), methods=["POST"])
        return router

    def _page(self, game: Game) -> Callable[[Request, str], Awaitable[Response]]:
        async def show_item(request: Request, number: str) -> Response:
            chosen = request.query_params
            return await self._render(
                request, game, number, chosen.get("hero"), chosen.get("price")
            )

        return show_item

    def _review(self, game: Game) -> Callable[[Request, str], Awaitable[Response]]:
        async def review(request: Request, number: str) -> Response:
            # Without scripts the buy panel sends its choice here, which the confirmation's
            # page answers, or the page again saying why the choice cannot be bought.
            form = await request.form()
            back = self._address(request, game, number)
            if signed_in(request) is None:
                return open_page(request, "/sign-in", back)
            if not form_matches(request, form):
                return toast_at(request, back, "web.store.refused.form", error=True)
            hero, price = str(form.get("hero", "")), str(form.get("price", ""))
            return await self._render(request, game, number, hero, price, review=True)

        return review

    def _buy(self, game: Game) -> Callable[[Request, str], Awaitable[Response]]:
        async def buy(request: Request, number: str) -> Response:
            form = await request.form()
            hero, price = str(form.get("hero", "")), str(form.get("price", ""))
            back = self._address(request, game, number, hero, price)
            player = signed_in(request)
            if player is None:
                return open_page(request, "/sign-in", back)
            if not purchase_matches(request, form):
                return toast_at(request, back, "web.store.refused.form", error=True)
            texts = QuoteTexts(game, request.state.language)
            offered = await self._offered(request, game, number, hero, price)
            if offered is None:
                return toast_at(request, back, "web.store.refused.gone", error=True)
            refused = self._refusal(texts, offered, str(form.get("paid", "")))
            if refused:
                return toast_at(request, back, refused[0], error=True, **refused[1])
            assert game.store is not None
            persona = offered.choice.hero.persona
            try:
                async with self._sent.once(form) as bought:
                    if bought:
                        toast(request, "web.store.done.again", error=False, **_paid(texts, offered))
                        return RedirectResponse(back, 303)
                    won = await self._purchases.purchase(
                        game, persona, game.store.product_id(offered.price.number)
                    )
            except PurchaseRefused as cause:
                key, values = await self._refused(request, game, texts, offered, cause.cause)
                return toast_at(request, back, key, error=True, **values)
            if won is not None:
                more = await self._reveal(request, game, texts, offered, won.product)
                toast(request, "web.store.done.box", error=False, **_paid(texts, offered), **more)
            else:
                key, more = await self._done(request, game, texts, offered)
                toast(request, key, error=False, **_paid(texts, offered), **more)
            return RedirectResponse(back, 303)

        return buy

    async def _render(
        self,
        request: Request,
        game: Game,
        number: str,
        hero: str | None,
        price: str | None,
        review: bool = False,
    ) -> Response:
        language = request.state.language
        sold = or_404(await self._items.find(game, _number(number), language))
        bar = await site_bar(request)
        roster = bar.roster if bar else None
        choices = await self._choices(request, game, sold, roster)
        texts = QuoteTexts(game, language)
        chosen = _chosen(choices, roster, hero)
        offered = None
        if chosen is not None:
            point = _price(sold, price) or _price(sold, number) or sold.prices[0]
            offered = quote(sold, chosen, point, datetime.now(UTC))
        if review and offered is not None and offered.buyable:
            return self._pages.render(
                request,
                "store.Confirm",
                game=game,
                offered=offered,
                texts=texts,
                back=self._address(request, game, number, hero or "", price or ""),
                csrf=form_token(request),
            )
        catalogue = await self._catalogues.of(game, language)
        art = self._pages.shows_art(request, game)
        numbers = [sold.item.id] if sold.item else [content.item_id for content in sold.contents]
        context: dict[str, Any] = dict(
            game=game,
            sold=sold,
            roster=roster,
            choices=choices,
            offered=offered,
            texts=texts,
            icons=[icon_url(game.id, catalogue, number, art) for number in numbers],
            reveal=request.session.pop(REVEAL, None) if not review else None,
            back=_back(game, sold, chosen, language),
            csrf=form_token(request),
        )
        return self._pages.render(request, "store.StoreItem", 400 if review else 200, **context)

    async def _offered(
        self, request: Request, game: Game, number: str, hero: str, price: str
    ) -> Quote | None:
        # What a confirmed purchase buys, for which of the signed-in account's heroes.
        sold = await self._items.find(game, _number(number), request.state.language)
        bar = await site_bar(request)
        roster = bar.roster if bar else None
        point = _price(sold, price) if sold else None
        if sold is None or point is None or roster is None:
            return None
        choices = await self._choices(request, game, sold, roster)
        chosen = or_404(next((c for c in choices if str(c.hero.persona.id) == hero), None))
        return quote(sold, chosen, point, datetime.now(UTC))

    def _refusal(
        self, texts: QuoteTexts, offered: Quote, paid: str
    ) -> tuple[str, dict[str, str]] | None:
        # A purchase the page would not have offered, checked before anything is spent.
        values = _paid(texts, offered)
        if offered.sold.ended and offered.sold.ends:
            return "web.store.refused.ended", values | {"moment": texts.moment(offered.sold.ends)}
        if paid != str(offered.price.price):
            return "web.store.refused.price", values
        if offered.choice.owned:
            return "web.store.refused.owned", values
        if offered.choice.unfit is Unfit.TAKEN:
            return "web.store.refused.taken", values
        if offered.choice.unfit:
            return "web.store.refused.unfit", values
        return None

    async def _refused(
        self, request: Request, game: Game, texts: QuoteTexts, offered: Quote, cause: str
    ) -> tuple[str, dict[str, str]]:
        # What the store refused, said in the rule's words with what was not spent.
        values = _paid(texts, offered)
        if cause == INSUFFICIENT_FUNDS:
            [now] = await self._choices(request, game, offered.sold, None, offered.choice)
            balance = now.balances.get(offered.price.currency, 0)
            shared = game.currencies[offered.price.currency].scope is Scope.ACCOUNT
            key = "web.store.refused.funds.account" if shared else "web.store.refused.funds.hero"
            return key, values | {"balance": texts.amount(balance)}
        if cause == ALREADY_OWNED:
            # The store adds nothing to an item owned for good, nor to one taken out of use.
            [now] = await self._choices(request, game, offered.sold, None, offered.choice)
            taken = now.unfit is Unfit.TAKEN
            return "web.store.refused." + ("taken" if taken else "owned"), values
        if cause in (NOT_FOR_HERO, LEVEL_MISSING):
            return "web.store.refused.unfit", values
        # A bundle no longer listed: its deal ended or was taken off, or the product is gone.
        again = await self._items.find(game, offered.sold.number, texts.language)
        if again and again.ended and again.ends:
            return "web.store.refused.ended", values | {"moment": texts.moment(again.ends)}
        return "web.store.refused.gone", values

    async def _reveal(
        self, request: Request, game: Game, texts: QuoteTexts, offered: Quote, won: Product
    ) -> dict[str, str]:
        # What a box gave, which the next page shows once, named in the page's language.
        catalogue = await self._catalogues.of(game, texts.language)
        name = catalogue.name(won.item_id) or str(won.item_id)
        request.session[REVEAL] = {
            "hero": offered.choice.hero.persona.name,
            "item": name,
            "icon": icon_url(game.id, catalogue, won.item_id, self._pages.shows_art(request, game)),
            "line": texts.text(
                "web.store.reveal.line",
                ownership=texts.ownership(won.ownership),
                worth=texts.priced(won.price, won.currency),
                price=texts.priced(offered.price.price, offered.price.currency),
            ),
        }
        return {"won": name, "ownership": texts.ownership(won.ownership, inline=True)}

    async def _done(
        self, request: Request, game: Game, texts: QuoteTexts, offered: Quote
    ) -> tuple[str, dict[str, str]]:
        # What the hero holds now, as the message after the purchase names it.
        sold = offered.sold
        if sold.kind is Kind.DEAL:
            return "web.store.done.deal", {}
        [now] = await self._choices(request, game, sold, None, offered.choice)
        held, gained = now.held, offered.price.ownership
        assert gained is not None
        if held and held.uses is not None and gained.uses:
            uses = texts.ownership(gained)
            return "web.store.done.uses", {
                "uses": uses,
                "left": texts.count("web.store.price.uses", held.uses),
            }
        if held and held.expires and gained.days:
            time = texts.ownership(gained)
            return "web.store.done.rental", {"time": time, "day": texts.day(held.expires)}
        return "web.store.done.permanent", {}

    async def _choices(
        self,
        request: Request,
        game: Game,
        sold: StoreItem,
        roster: Roster | None,
        only: HeroChoice | None = None,
    ) -> list[HeroChoice]:
        player = signed_in(request)
        heroes = [only.hero] if only else roster.heroes if roster else []
        if player is None or not heroes:
            return []
        return await self._items.choices(game, sold, player.account_id, heroes)

    def _address(
        self, request: Request, game: Game, number: str, hero: str = "", price: str = ""
    ) -> str:
        page = localize(f"/{game.id}{ITEM.format(number=_number(number))}", request.state.language)
        chosen = {key: value for key, value in (("hero", hero), ("price", price)) if value}
        return f"{page}?{urlencode(chosen)}" if chosen else page


def _back(
    game: Game, sold: StoreItem, chosen: HeroChoice | None, language: str
) -> tuple[str, str] | None:
    # The list a page is found in and its name: the store front, with the hero an item was
    # chosen for, or the hot deals.
    kind: PageKind = "store" if sold.kind is Kind.ITEM else "hot-deals"
    if game.site is None or kind not in game.pages:
        return None
    page = localize(f"/{game.id}{game.site.path(kind)}", language)
    hero = f"?hero={chosen.hero.persona.id}" if chosen and kind == "store" else ""
    return page + hero, translate(f"web.site.page.{kind}", language)


def _number(text: str) -> int:
    # A number the database holds, of at most 18 digits; anything else finds no page.
    if not (text.isascii() and text.isdecimal() and len(text) <= 18):
        raise HTTPException(404)
    return int(text)


def _price(sold: StoreItem | None, chosen: str | None) -> PricePoint | None:
    if sold is None or not chosen:
        return None
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


def _paid(texts: QuoteTexts, offered: Quote) -> dict[str, str]:
    return {
        "item": offered.sold.name,
        "hero": offered.choice.hero.persona.name,
        "price": texts.amount(offered.price.price),
        "currency": texts.currency(offered.price.currency),
    }
