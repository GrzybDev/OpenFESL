from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from babel import Locale
from babel.lists import format_list
from babel.numbers import format_percent
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.chests import ChestDrop, ChestRefused, ChestRules, Chests, key_tag
from openfesl.core.entitlements import ACTIVE, EntitlementQuery, EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero, Roster
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership
from openfesl.core.rewards import FIRST_CHEST, Rewards, RewardState
from openfesl.core.texts import localize
from openfesl.core.wallets import Wallets
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, toast
from openfesl.web.site import site_bar
from openfesl.web.store_texts import StoreTexts

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
        wallets: Wallets,
        rewards: Rewards,
    ) -> None:
        """Prepare the treasure chests page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            chests: Tells what the chests give and what a key costs.
            entitlements: Hold the account's keys and its heroes' chests.
            catalogues: Name the items the chests give, once imported.
            wallets: Tell what the account has to pay keys with, opening nothing.
            rewards: Tell whether the account's first key waits to be claimed.
        """
        self._pages = pages
        self._games = games
        self._chests = chests
        self._entitlements = entitlements
        self._catalogues = catalogues
        self._wallets = wallets
        self._rewards = rewards

    def routes(self) -> APIRouter:
        """Return the treasure chests page of each game that lists it, and its forms."""
        router = APIRouter()
        for game in self._games.with_page("treasure-chests"):
            base = f"/{game.id}{CHESTS}"
            router.add_api_route(base, self._page(game), response_class=HTMLResponse)
            router.add_api_route(base + "/{hero_id}/open", self._open(game), methods=["POST"])
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_chests(request: Request) -> Response:
            language = request.state.language
            bar = await site_bar(request)
            player = signed_in(request)
            key = await self._chests.key(game)
            heroes, keys, funds, waits = None, 0, 0, False
            if bar and bar.roster is not None and player:
                keys, heroes = await self._held(_rules(game), player.account_id, bar.roster)
                waits = any(
                    reward.key == FIRST_CHEST and reward.state is RewardState.CLAIMABLE
                    for reward in await self._rewards.available(game, player.account_id)
                )
                if key:
                    [funds] = await self._wallets.of_account(
                        game, player.account_id, [key.currency]
                    )
            catalogue = await self._catalogues.of(game, language)
            return self._pages.render(
                request,
                "chests.Chests",
                game=game,
                heroes=heroes,
                keys=keys,
                key=key,
                funds=funds,
                waits=waits,
                lists=await self._lists(StoreTexts(game, language, datetime.now(UTC)), catalogue),
                imported=catalogue.imported,
                next=localize(f"/{game.id}{CHESTS}", language),
                csrf=form_token(request) if player else "",
            )

        return show_chests

    def _open(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def open_chest(request: Request, hero_id: int) -> Response:
            form = await request.form()
            language = request.state.language
            page = localize(f"/{game.id}{CHESTS}", language)
            bar = await site_bar(request)
            if bar is None or bar.roster is None:
                return RedirectResponse(page, 303)
            hero = next((hero for hero in bar.roster.heroes if hero.persona.id == hero_id), None)
            if hero is None:
                raise HTTPException(404)
            name = hero.persona.name
            if not form_matches(request, form):
                toast(request, "web.chests.refused.form", error=True, hero=name)
                return RedirectResponse(page, 303)
            try:
                won = await self._chests.open(game, hero.persona)
            except ChestRefused as refused:
                toast(request, f"web.chests.refused.{refused.refusal}", error=True, hero=name)
                return RedirectResponse(page, 303)
            catalogue = await self._catalogues.of(game, language)
            key = await self._chests.key(game)
            keys = await held_keys(self._entitlements, _rules(game), hero.persona.account_id)
            texts = StoreTexts(game, language, datetime.now(UTC))
            worth = {"worth": texts.priced(won.price, won.currency)}
            if key:
                worth["price"] = texts.priced(key.price, key.currency)
            toast(
                request,
                "web.chests.opened",
                error=False,
                hero=name,
                item=catalogue.name(won.item_id) or str(won.item_id),
                term=_term(won.ownership, texts),
                keys=texts.count("web.chests.opened.keys", keys)
                if keys
                else texts.text("web.chests.opened.keys.none"),
                # What the reveal on the next page says of the item and whose it is.
                line=texts.text(
                    "web.chests.reveal.worth" + ("" if key else ".unsold"),
                    term=_term(won.ownership, texts, start=True),
                    **worth,
                ),
                hero_id=str(hero_id),
            )
            return RedirectResponse(page, 303)

        return open_chest

    async def _held(
        self, rules: ChestRules, account_id: int, roster: Roster
    ) -> tuple[int, list[HeroChests]]:
        # The account's keys, and the chests each of its heroes has to open.
        heroes = []
        for hero in roster.heroes:
            stacks = await self._entitlements.of_persona(hero.persona.id, rules.tag)
            stack = stacks[0] if stacks else None
            count = (stack.uses or 0) if stack and stack.status == ACTIVE else 0
            heroes.append(HeroChests(hero, count, stack.granted if stack else None))
        return await held_keys(self._entitlements, rules, account_id), heroes

    async def _lists(self, texts: StoreTexts, catalogue: Catalogue) -> ChestLists | None:
        odds = await self._chests.odds(texts.game)
        if odds is None:
            return None
        worth = texts.priced(odds.worth, odds.currency) if odds.worth else ""
        factions = {
            faction: [_line(drop, catalogue, texts) for drop in drops]
            for faction, drops in odds.factions.items()
        }
        return ChestLists(worth, factions)


def _term(ownership: Ownership, texts: StoreTexts, start: bool = False) -> str:
    # For how long or for how many uses a hero gets an item, within a sentence or starting one.
    if ownership.type == PERMANENT and start:
        return texts.text("web.chests.held.permanent.start")
    if ownership.type == RENTED and ownership.days:
        return texts.count("web.chests.held.days", ownership.days)
    if ownership.type == COUNTED and ownership.uses:
        return texts.count("web.chests.held.uses", ownership.uses)
    return texts.text("web.chests.held.permanent")


def _line(drop: ChestDrop, catalogue: Catalogue, texts: StoreTexts) -> DropLine:
    locale = Locale.parse(texts.text("web.locale"))
    names = [catalogue.name(product.item_id) or str(product.item_id) for product in drop.products]
    name = names[0]
    if len(names) > 1:
        # One item of several, by the class of the hero that opens the chest.
        joined = str(format_list(names, style="or", locale=locale))
        name = texts.text("web.chests.lists.by_class", names=joined)
    low, high = (
        str(format_percent(float(value), "#,##0.#%", locale=locale)) for value in drop.chance
    )
    chance = low
    if low != high and not drop.chance[0]:
        chance = texts.text("web.chests.chance.up_to", chance=high)
    elif low != high:
        chance = texts.text("web.chests.chance.range", low=low, high=high)
    return DropLine(name, _term(drop.products[0].ownership, texts), chance)


async def held_keys(entitlements: EntitlementStore, rules: ChestRules, account_id: int) -> int:
    """Return how many keys an account holds.

    Args:
        entitlements: Where the account's keys are kept.
        rules: The game's chest rules, which name the key.
        account_id: The account.
    """
    query = EntitlementQuery(tag=key_tag(rules), status=ACTIVE)
    return sum(entry.uses or 0 for entry in await entitlements.of_account(account_id, query))


def _rules(game: Game) -> ChestRules:
    assert game.chests is not None
    return game.chests
