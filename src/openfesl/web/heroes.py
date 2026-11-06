from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.chests import Chests
from openfesl.core.entitlements import ACTIVE, EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.hero_items import held
from openfesl.core.heroes import Hero, Heroes, HeroRefused, Refusal, Roster
from openfesl.core.store_window import running_low
from openfesl.core.texts import localize, translate
from openfesl.core.wallets import Wallets
from openfesl.web.account.session import signed_in, to_sign_in
from openfesl.web.pages import Pages, number
from openfesl.web.sessions import (
    SentForms,
    form_matches,
    form_token,
    inside,
    purchase_matches,
    toast,
    toast_at,
)
from openfesl.web.site import site_bar

HEROES = "/heroes"
SLOTS = "/user/buyCharacterSlots"


@dataclass(frozen=True, slots=True)
class HeroCard:
    """A hero as the heroes page shows it on its card.

    Attributes:
        hero: The hero.
        slot: Its slot, from 1.
        default: Whether the game is started as it.
        xp_needed: The XP its level needs to the next, from the catalogue's rank table; none
            without a catalogue or at the highest level.
        points: The Hero Points it has to spend, and those it earned in all.
        items: The items it holds: the starting kit of its faction and class and those kept for
            it while they are in use and have time left.
        low: How many of the items kept for it run low: few uses, days or hours left.
        chests: How many treasure chests it has to open; none in a game without them.
    """

    hero: Hero
    slot: int
    default: bool
    xp_needed: int | None
    points: tuple[int, int]
    items: int
    low: int
    chests: int | None


class HeroesPages:
    """The page of an account's heroes, and the choice of the hero the game is started as."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        heroes: Heroes,
        sent: SentForms,
        wallets: Wallets,
        entitlements: EntitlementStore,
        catalogues: Catalogues,
        chests: Chests,
    ) -> None:
        """Prepare the heroes page of each game whose heroes are made on its site.

        Args:
            pages: Renders the pages.
            games: The games, of which those with hero rules get the page.
            heroes: Lists the heroes and keeps the default one.
            sent: Lets each form that buys a hero slot buy once.
            wallets: Reads the heroes' Hero Points without opening their wallets.
            entitlements: Lists the items and treasure chests kept for each hero.
            catalogues: Give the XP each level needs, once imported.
            chests: Tell what a key costs and what a chest's item is worth.
        """
        self._pages = pages
        self._games = games
        self._heroes = heroes
        self._sent = sent
        self._wallets = wallets
        self._entitlements = entitlements
        self._catalogues = catalogues
        self._chests = chests

    def routes(self) -> APIRouter:
        """Return each game's heroes page and its forms of the default hero, new names and slots."""
        router = APIRouter()
        for game in self._games:
            if game.heroes:
                base = f"/{game.id}{HEROES}"
                router.add_api_route(base, self._page(game), response_class=HTMLResponse)
                router.add_api_route(
                    base + "/{hero_id}/default", self._default(game), methods=["POST"]
                )
                router.add_api_route(
                    base + "/{hero_id}/rename",
                    self._rename_page(game),
                    response_class=HTMLResponse,
                )
                router.add_api_route(
                    base + "/{hero_id}/rename", self._rename(game), methods=["POST"]
                )
                slots = f"/{game.id}{SLOTS}"
                router.add_api_route(slots, self._slots_page(game), response_class=HTMLResponse)
                router.add_api_route(slots, self._buy_slot(game), methods=["POST"])
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_heroes(request: Request) -> Response:
            bar = await site_bar(request)
            if bar is None or bar.roster is None:
                back = localize(f"/{game.id}{HEROES}", request.state.language)
                return to_sign_in(request, back, "needed", page="heroes")
            catalogue = await self._catalogues.of(game, request.state.language)
            return self._pages.render(
                request,
                "heroes.Heroes",
                game=game,
                roster=bar.roster,
                cards=await hero_cards(
                    game, bar.roster, catalogue, self._wallets, self._entitlements
                ),
                funds=bar.funds,
                key=await self._chests.key(game),
                worth=await self._chests.worth(game),
                csrf=form_token(request),
            )

        return show_heroes

    def _default(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def make_default(request: Request, hero_id: int) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), ("/",))
            back = back or localize(f"/{game.id}{HEROES}", request.state.language)
            player = signed_in(request)
            if player is None or not form_matches(request, form):
                return RedirectResponse(back, 303)
            roster = await self._heroes.roster(game, player.account_id)
            hero = next((h for h in roster.heroes if h.persona.id == hero_id), None)
            if hero and await self._heroes.make_default(game, player.account_id, hero_id):
                name = hero.persona.name
                # The next page shows the stamp of the hero that was the default one leaving.
                was = roster.default.persona.name if roster.default else ""
                toast(request, "web.heroes.toast.default", hero=name, game=game.name, previous=was)
            return RedirectResponse(back, 303)

        return make_default

    def _rename_page(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def show_rename(request: Request, hero_id: int) -> Response:
            found = await self._own(request, game, hero_id)
            if not isinstance(found, Hero):
                return found
            return await self._render_rename(request, game, found)

        return show_rename

    def _rename(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def rename(request: Request, hero_id: int) -> Response:
            form = await request.form()
            found = await self._own(request, game, hero_id)
            if not isinstance(found, Hero):
                return found
            name = str(form.get("name", "")).strip()
            old = found.persona.name
            price = (await self._heroes.rules(game)).rename_price
            heroes = localize(f"/{game.id}{HEROES}", request.state.language)
            if not form_matches(request, form):
                return toast_at(request, heroes, "web.rename.refused.form", error=True, hero=old)
            if str(form.get("price", "")) != str(price):
                values = self._prices(game, 0, request.state.language, price)
                toast(request, "web.rename.refused.price", error=True, hero=old, **values)
                return RedirectResponse(heroes, 303)
            try:
                await self._heroes.rename(game, found.persona, name, price)
            except HeroRefused as refused:
                if refused.refusal is not Refusal.FUNDS:
                    return await self._render_rename(request, game, found, name, refused.refusal)
                funds = await self._funds(game, found.persona.account_id)
                values = self._prices(game, funds, request.state.language, price)
                toast(request, "web.rename.refused.funds", error=True, hero=old, **values)
                return RedirectResponse(heroes, 303)
            prices = self._prices(game, 0, request.state.language, price)
            toast(
                request, "web.rename.done", error=False, old=old, new=name, game=game.name, **prices
            )
            return RedirectResponse(heroes, 303)

        return rename

    def _slots_page(self, game: Game) -> Callable[[Request, str], Awaitable[Response]]:
        async def show_slots(request: Request, next: str = "") -> Response:
            if signed_in(request) is None:
                back = localize(f"/{game.id}{SLOTS}", request.state.language)
                return to_sign_in(request, back, "needed", page="heroes")
            return self._pages.render(
                request,
                "heroes.BuySlots",
                game=game,
                next=inside(next, ("/",)),
                csrf=form_token(request),
            )

        return show_slots

    def _buy_slot(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def buy_slot(request: Request) -> Response:
            form = await request.form()
            heroes = localize(f"/{game.id}{HEROES}", request.state.language)
            back = inside(str(form.get("next", "")), ("/",)) or heroes
            player = signed_in(request)
            if player is None or not purchase_matches(request, form):
                return toast_at(request, back, "web.slots.refused.form", error=True)
            price = (await self._heroes.rules(game)).slot_price
            language = request.state.language
            if str(form.get("price", "")) != str(price):
                values = self._prices(game, 0, language, price)
                return toast_at(request, back, "web.slots.refused.price", error=True, **values)
            try:
                async with self._sent.once(form) as bought:
                    if bought:
                        return toast_at(request, back, "web.slots.done.again", error=False)
                    slot = await self._heroes.buy_slot(game, player.account_id, price)
            except HeroRefused:
                funds = await self._funds(game, player.account_id)
                values = self._prices(game, funds, language, price)
                return toast_at(request, back, "web.slots.refused.funds", error=True, **values)
            values = self._prices(game, 0, language, price)
            slot_number = number(slot, language)
            toast(
                request, "web.slots.done", error=False, slot=slot_number, spent=str(price), **values
            )
            return RedirectResponse(back, 303)

        return buy_slot

    async def _own(self, request: Request, game: Game, hero_id: int) -> Hero | Response:
        # The signed-in account's hero, or the sign-in page, or no page for another's hero.
        bar = await site_bar(request)
        if bar is None or bar.roster is None:
            back = localize(f"/{game.id}{HEROES}", request.state.language)
            return to_sign_in(request, back, "needed", page="heroes")
        hero = next((hero for hero in bar.roster.heroes if hero.persona.id == hero_id), None)
        if hero is None:
            raise HTTPException(404)
        return hero

    async def _render_rename(
        self, request: Request, game: Game, hero: Hero, name: str = "", refused: str = ""
    ) -> Response:
        # The hero was found among the bar's, which was read for this request.
        bar = await site_bar(request)
        assert bar is not None
        return self._pages.render(
            request,
            "heroes.Rename",
            409 if refused == Refusal.TAKEN else 400 if refused else 200,
            game=game,
            hero=hero,
            funds=bar.funds,
            name=name,
            check=refused,
            csrf=form_token(request),
        )

    async def _funds(self, game: Game, account_id: int) -> int:
        # What the account has to pay prices with, its wallet left unopened until it pays.
        assert game.heroes is not None
        [funds] = await self._wallets.of_account(game, account_id, [game.heroes.currency])
        return funds

    def _prices(self, game: Game, funds: int, language: str, price: int) -> dict[str, str]:
        # The amounts a message names, written as the page's language writes them.
        assert game.heroes is not None
        currency = translate(f"games.{game.id}.currency.{game.heroes.currency}", language)
        return {
            "price": number(price, language),
            "funds": number(funds, language),
            "currency": currency,
        }


async def hero_cards(
    game: Game,
    roster: Roster,
    catalogue: Catalogue,
    wallets: Wallets,
    entitlements: EntitlementStore,
) -> list[HeroCard]:
    """Return what the heroes' cards show of an account's heroes, in their slots' order.

    Args:
        game: The game, which has hero rules.
        roster: The account's heroes.
        catalogue: The game's catalogue, which gives the XP each level needs once imported.
        wallets: Reads the heroes' Hero Points without opening their wallets.
        entitlements: Lists the items and treasure chests kept for each hero.
    """
    rules = game.heroes
    assert rules is not None
    personas = [hero.persona for hero in roster.heroes]
    points = await wallets.of_personas(game, personas, rules.points)
    cards = []
    for slot, (hero, earned) in enumerate(zip(roster.heroes, points, strict=True), 1):
        kept = await entitlements.of_persona(hero.persona.id)
        owned = held(rules, hero.faction, hero.hero_class, kept)
        chests = game.chests.tag if game.chests else None
        stacks = [item.uses or 0 for item in kept if item.tag == chests and item.status == ACTIVE]
        cards.append(
            HeroCard(
                hero=hero,
                slot=slot,
                default=hero.persona.id == roster.default_id,
                xp_needed=catalogue.xp_to_next(hero.level),
                points=earned,
                items=len(owned),
                low=running_low(rules, kept, datetime.now(UTC)),
                chests=sum(stacks) if chests else None,
            )
        )
    return cards
