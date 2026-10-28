from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from babel import Locale
from babel.lists import format_list
from babel.numbers import format_percent
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.chests import ChestDrop, ChestRules, Chests, key_tag
from openfesl.core.entitlements import ACTIVE, EntitlementQuery, EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero, Roster
from openfesl.core.inventory import COUNTED, RENTED, Ownership
from openfesl.core.texts import localize, translate, translate_count
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages, number
from openfesl.web.site import site_bar

CHESTS = "/treasure-chests"


@dataclass(frozen=True, slots=True)
class HeroChests:
    """A hero's treasure chests as the page shows them.

    Attributes:
        hero: The hero.
        count: How many chests it has to open.
        found: When it last found one; none before its first.
    """

    hero: Hero
    count: int
    found: datetime | None


@dataclass(frozen=True, slots=True)
class DropLine:
    """One of the things a chest can give, as the page lists it.

    Attributes:
        name: The item's name, or the names of the items of which a hero gets the one that fits
            its class.
        held: For how long or for how many uses the hero gets it.
        chance: Its chance, as the page's language writes it.
    """

    name: str
    held: str
    chance: str


@dataclass(frozen=True, slots=True)
class ChestLists:
    """What a chest can give the heroes of each faction, as the page lists it.

    Attributes:
        worth: What every item a chest gives is worth at least, with its currency; empty where
            chests do not promise it.
        factions: The lines by faction, in the order of the game's factions.
    """

    worth: str
    factions: dict[str, list[DropLine]]


class ChestsPages:
    """The page of the treasure chests that an account's heroes found, and of its keys."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        chests: Chests,
        entitlements: EntitlementStore,
        catalogues: Catalogues,
    ) -> None:
        """Prepare the treasure chests page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            chests: Tells what the chests give and what a key costs.
            entitlements: Hold the account's keys and its heroes' chests.
            catalogues: Name the items the chests give, once imported.
        """
        self._pages = pages
        self._games = games
        self._chests = chests
        self._entitlements = entitlements
        self._catalogues = catalogues

    def routes(self) -> APIRouter:
        """Return the treasure chests page of each game that lists it."""
        router = APIRouter()
        for game in self._games.with_page("treasure-chests"):
            router.add_api_route(
                f"/{game.id}{CHESTS}", self._page(game), response_class=HTMLResponse
            )
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_chests(request: Request) -> Response:
            language = request.state.language
            bar = await site_bar(request)
            player = signed_in(request)
            heroes, keys = None, 0
            if bar and bar.roster is not None and player:
                keys, heroes = await self._held(_rules(game), player.account_id, bar.roster)
            catalogue = await self._catalogues.of(game, language)
            return self._pages.render(
                request,
                "chests.Chests",
                game=game,
                heroes=heroes,
                keys=keys,
                key=await self._chests.key(game),
                lists=await self._lists(game, catalogue, language),
                imported=catalogue.imported,
                next=localize(f"/{game.id}{CHESTS}", language),
            )

        return show_chests

    async def _held(
        self, rules: ChestRules, account_id: int, roster: Roster
    ) -> tuple[int, list[HeroChests]]:
        # The account's keys, and the chests each of its heroes has to open.
        query = EntitlementQuery(tag=key_tag(rules), status=ACTIVE)
        kept = await self._entitlements.of_account(account_id, query)
        heroes = []
        for hero in roster.heroes:
            stacks = await self._entitlements.of_persona(hero.persona.id, rules.tag)
            stack = stacks[0] if stacks else None
            count = (stack.uses or 0) if stack and stack.status == ACTIVE else 0
            heroes.append(HeroChests(hero, count, stack.granted if stack else None))
        return sum(entry.uses or 0 for entry in kept), heroes

    async def _lists(self, game: Game, catalogue: Catalogue, language: str) -> ChestLists | None:
        odds = await self._chests.odds(game)
        if odds is None:
            return None
        currency = translate(f"games.{game.id}.currency.{odds.currency}", language)
        worth = f"{number(odds.worth, language)} {currency}" if odds.worth else ""
        factions = {
            faction: [_line(drop, catalogue, language) for drop in drops]
            for faction, drops in odds.factions.items()
        }
        return ChestLists(worth, factions)


def _term(ownership: Ownership, language: str) -> str:
    # For how long or for how many uses a hero gets an item, as part of a sentence.
    if ownership.type == RENTED and ownership.days:
        return _count("web.chests.held.days", ownership.days, language)
    if ownership.type == COUNTED and ownership.uses:
        return _count("web.chests.held.uses", ownership.uses, language)
    return translate("web.chests.held.permanent", language)


def _line(drop: ChestDrop, catalogue: Catalogue, language: str) -> DropLine:
    locale = Locale.parse(translate("web.locale", language))
    names = [catalogue.name(product.item_id) or str(product.item_id) for product in drop.products]
    name = names[0]
    if len(names) > 1:
        # One item of several, by the class of the hero that opens the chest.
        joined = str(format_list(names, style="or", locale=locale))
        name = translate("web.chests.lists.by_class", language) % {"names": joined}
    low, high = (
        str(format_percent(float(value), "#,##0.#%", locale=locale)) for value in drop.chance
    )
    chance = low
    if low != high and not drop.chance[0]:
        chance = translate("web.chests.chance.up_to", language) % {"chance": high}
    elif low != high:
        chance = translate("web.chests.chance.range", language) % {"low": low, "high": high}
    return DropLine(name, _term(drop.products[0].ownership, language), chance)


def _count(key: str, count: int, language: str) -> str:
    return translate_count(key, count, language) % {"count": number(count, language)}


def _rules(game: Game) -> ChestRules:
    assert game.chests is not None
    return game.chests
