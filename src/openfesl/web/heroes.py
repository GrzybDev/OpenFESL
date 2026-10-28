from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogues
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero, Heroes, HeroRefused, Refusal, Roster
from openfesl.core.texts import localize, translate
from openfesl.core.wallets import Wallets
from openfesl.web.account.session import open_page, signed_in
from openfesl.web.pages import Pages, number
from openfesl.web.sessions import form_matches, form_token, inside, notify, toast

HEROES = "/heroes"


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
        items: The items it owns: the starting kit of its faction and class and those kept for
            it.
        low: How many of the items kept for it have few uses left.
    """

    hero: Hero
    slot: int
    default: bool
    xp_needed: int | None
    points: tuple[int, int]
    items: int
    low: int


class HeroesPages:
    """The page of an account's heroes, and the choice of the hero the game is started as."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        heroes: Heroes,
        wallets: Wallets,
        entitlements: EntitlementStore,
        catalogues: Catalogues,
    ) -> None:
        """Prepare the heroes page of each game whose heroes are made on its site.

        Args:
            pages: Renders the pages.
            games: The games, of which those with hero rules get the page.
            heroes: Lists the heroes and keeps the default one.
            wallets: Reads the heroes' Hero Points without opening their wallets.
            entitlements: Lists the items kept for each hero.
            catalogues: Give the XP each level needs, once imported.
        """
        self._pages = pages
        self._games = games
        self._heroes = heroes
        self._wallets = wallets
        self._entitlements = entitlements
        self._catalogues = catalogues

    def routes(self) -> APIRouter:
        """Return each game's heroes page and its forms of the default hero and of new names."""
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
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_heroes(request: Request) -> Response:
            player = signed_in(request)
            if player is None:
                notify(request, "needed", page="heroes")
                back = localize(f"/{game.id}{HEROES}", request.state.language)
                return open_page(request, "/sign-in", back)
            roster = await self._heroes.roster(game, player.account_id)
            return self._pages.render(
                request,
                "heroes.Heroes",
                game=game,
                roster=roster,
                cards=await self._cards(game, roster, request.state.language),
                funds=await self._funds(game, player.account_id),
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
            heroes = localize(f"/{game.id}{HEROES}", request.state.language)
            if not form_matches(request, form):
                toast(request, "web.rename.refused.form", error=True, hero=old)
                return RedirectResponse(heroes, 303)
            try:
                await self._heroes.rename(game, found.persona, name)
            except HeroRefused as refused:
                if refused.refusal is not Refusal.FUNDS:
                    return await self._render_rename(request, game, found, name, refused.refusal)
                funds = await self._funds(game, found.persona.account_id)
                values = self._prices(game, funds, request.state.language)
                toast(request, "web.rename.refused.funds", error=True, hero=old, **values)
                return RedirectResponse(heroes, 303)
            prices = self._prices(game, 0, request.state.language)
            toast(
                request, "web.rename.done", error=False, old=old, new=name, game=game.name, **prices
            )
            return RedirectResponse(heroes, 303)

        return rename

    async def _own(self, request: Request, game: Game, hero_id: int) -> Hero | Response:
        # The signed-in account's hero, or the sign-in page, or no page for another's hero.
        player = signed_in(request)
        if player is None:
            notify(request, "needed", page="heroes")
            back = localize(f"/{game.id}{HEROES}", request.state.language)
            return open_page(request, "/sign-in", back)
        roster = await self._heroes.roster(game, player.account_id)
        hero = next((hero for hero in roster.heroes if hero.persona.id == hero_id), None)
        if hero is None:
            raise HTTPException(404)
        return hero

    async def _render_rename(
        self, request: Request, game: Game, hero: Hero, name: str = "", refused: str = ""
    ) -> Response:
        return self._pages.render(
            request,
            "heroes.Rename",
            409 if refused == Refusal.TAKEN else 400 if refused else 200,
            game=game,
            hero=hero,
            funds=await self._funds(game, hero.persona.account_id),
            name=name,
            check=refused,
            csrf=form_token(request),
        )

    async def _funds(self, game: Game, account_id: int) -> int:
        # What the account has to pay prices with, its wallet left unopened until it pays.
        assert game.heroes is not None
        [funds] = await self._wallets.of_account(game, account_id, [game.heroes.currency])
        return funds

    def _prices(self, game: Game, funds: int, language: str) -> dict[str, str]:
        # The amounts a message names, written as the page's language writes them.
        assert game.heroes is not None
        currency = translate(f"games.{game.id}.currency.{game.heroes.currency}", language)
        return {
            "price": number(game.heroes.rename_price, language),
            "funds": number(funds, language),
            "currency": currency,
        }

    async def _cards(self, game: Game, roster: Roster, language: str) -> list[HeroCard]:
        rules = game.heroes
        assert rules is not None
        catalogue = await self._catalogues.of(game, language)
        personas = [hero.persona for hero in roster.heroes]
        points = await self._wallets.of_personas(game, personas, rules.points)
        cards = []
        for slot, (hero, earned) in enumerate(zip(roster.heroes, points, strict=True), 1):
            kept = await self._entitlements.of_persona(hero.persona.id)
            owned = {int(item.tag.partition("-")[0]) for item in kept if item.status == "ACTIVE"}
            if hero.faction and hero.hero_class:
                loadout = rules.loadouts[hero.faction][hero.hero_class]
                owned |= {*loadout.appearance, *loadout.equipment, *loadout.emotes}
            low = sum(
                1
                for item in kept
                if item.status == "ACTIVE"
                and item.uses is not None
                and item.uses <= rules.running_low
            )
            cards.append(
                HeroCard(
                    hero=hero,
                    slot=slot,
                    default=hero.persona.id == roster.default_id,
                    xp_needed=catalogue.xp_to_next(hero.level),
                    points=earned,
                    items=len(owned),
                    low=low,
                )
            )
        return cards
