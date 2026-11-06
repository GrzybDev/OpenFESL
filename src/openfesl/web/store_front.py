import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import QueryParams

from openfesl.core.catalogues import Catalogue, Catalogues, Item
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero
from openfesl.core.store_front import FrontItem, Owned, StoreFront
from openfesl.core.store_items import Requirement, Unfit, requirement
from openfesl.core.store_window import DAYS, HOURS, USES
from openfesl.core.texts import localize
from openfesl.web.catalogue_files import icon_url
from openfesl.web.pages import Pages, query_of
from openfesl.web.site import site_bar
from openfesl.web.store import ITEM
from openfesl.web.store_texts import StoreTexts

PAGE_SIZE = 48
# The longest search the page takes; item names are far shorter.
MAX_QUERY = 64


@dataclass(frozen=True, slots=True)
class Price:
    """An item's lowest price in one currency, as a tile shows it.

    Attributes:
        currency: The currency's code.
        amount: The price as the page's language writes it.
        name: The currency's name.
        glyph: The glyph on its coin.
    """

    currency: str
    amount: str
    name: str
    glyph: str


@dataclass(frozen=True, slots=True)
class Tile:
    """An item as a tile of the store front shows it.

    Attributes:
        name: Its name in the page's language, else in English, else its number.
        kind: Its kind, such as weapon.
        href: The address of its page; none for an item the store does not sell.
        icon: The address of its imported icon, where the page shows the game's art.
        requires: Which heroes may have it.
        prices: Its lowest price in each currency; none for an item the store does not sell.
        line: What the chosen hero holds of it, or why it cannot have it yet; empty without a
            hero.
        state: How the line is marked: held, low, level or none.
        unsold: Why the store does not sell it, for an item a hero holds that it does not.
    """

    name: str
    kind: str
    href: str | None
    icon: str | None
    requires: str
    prices: list[Price]
    line: str
    state: str
    unsold: str = ""


@dataclass(frozen=True, slots=True)
class Choice:
    """The choices of the store front's filter, as its address names them.

    Attributes:
        hero: The hero whose items are listed; none without one.
        kind: The kind of items listed; every kind when empty.
        owned: Whether only the items the hero holds are listed.
        search: Part of an item's name, or its number.
        page: The page of the list, from 1.
    """

    hero: Hero | None
    kind: str = ""
    owned: bool = False
    search: str = ""
    page: int = 1

    def changed(self, **changes: Any) -> Choice:
        """Return the choices with some changed, on the first page unless the page is one.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **({"page": 1} | changes))

    def query(self) -> str:
        """Return the query of the store front's address for these choices, with a leading ?."""
        return query_of(
            ("hero", str(self.hero.persona.id) if self.hero else ""),
            ("kind", self.kind),
            ("owned", "1" if self.owned else ""),
            ("q", self.search),
            ("page", str(self.page) if self.page > 1 else ""),
        )


@dataclass(frozen=True, slots=True)
class Empty:
    """What the store front says when its list is empty, and the ways out.

    Attributes:
        title: What is missing.
        text: Why, if the page knows.
        glyph: The glyph of its disc.
        links: The ways out, as label and query of the address.
    """

    title: str
    text: str
    glyph: str
    links: list[tuple[str, str]] = field(default_factory=list)


class StoreFrontPages:
    """The store front of a game's site: every item its store sells alone, for one hero."""

    def __init__(
        self, pages: Pages, games: Games, front: StoreFront, catalogues: Catalogues
    ) -> None:
        """Prepare the store front of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the store get it.
            front: Says what each store sells alone and what each hero holds.
            catalogues: Name the items in the page's language.
        """
        self._pages = pages
        self._games = games
        self._front = front
        self._catalogues = catalogues

    def routes(self) -> APIRouter:
        """Return the store front of each game that lists it."""
        router = APIRouter()
        for game in self._games.with_page("store"):
            assert game.site is not None
            path = f"/{game.id}{game.site.path('store')}"
            router.add_api_route(path, self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_store(request: Request) -> Response:
            language = request.state.language
            texts = StoreTexts(game, language)
            bar = await site_bar(request)
            roster = bar.roster if bar else None
            heroes = roster.heroes if roster else []
            items = await self._front.items(game)
            kinds = _kinds(game, items)
            choice = _choice(request.query_params, heroes, roster.default_id if roster else None)
            choice = choice.changed(
                kind=choice.kind if choice.kind in kinds else "",
                owned=choice.owned and choice.hero is not None,
                page=choice.page,
            )
            hero = choice.hero
            fitting = [item for item in items if hero is None or _fits(item.requirement, hero)]
            counts = {kind: sum(item.item.kind == kind for item in fitting) for kind in kinds}
            counted = {item.item.id for item in items if item.uses}
            held = await self._front.holdings(game, hero, counted) if hero else {}
            catalogue = await self._catalogues.of(game, language)
            pool = (
                _inventory(items, catalogue, held)
                if choice.owned
                else [(item, item.item) for item in fitting]
            )
            if choice.kind:
                pool = [(found, item) for found, item in pool if item.kind == choice.kind]
            if choice.search:
                matches = await self._front.matching(game, choice.search)
                pool = [(found, item) for found, item in pool if item.id in matches]
            pages = max(math.ceil(len(pool) / PAGE_SIZE), 1)
            choice = choice.changed(page=min(choice.page, pages))
            shown = pool[(choice.page - 1) * PAGE_SIZE : choice.page * PAGE_SIZE]
            art = self._pages.shows_art(request, game)
            buy = localize(f"/{game.id}{ITEM.format(number='')}", language)
            tiles = [
                tile(
                    texts,
                    catalogue,
                    found,
                    item,
                    hero,
                    held.get(item.id),
                    buy,
                    icon_url(game.id, catalogue, item.id, art),
                )
                for found, item in shown
            ]
            return self._pages.render(
                request,
                "store.StoreFront",
                game=game,
                heroes=heroes,
                choice=choice,
                kinds=[("", len(fitting))] + [(kind, counts[kind]) for kind in kinds],
                glyphs=kind_glyphs(game),
                tiles=tiles,
                total=len(pool),
                pages=pages,
                size=PAGE_SIZE,
                low=sum(held[item.id].low for _, item in pool) if choice.owned else 0,
                on_sale=len(items),
                empty=None
                if pool
                else await self._empty(texts, game, items, choice, heroes, catalogue.imported),
                here=localize(f"/{game.id}{game.site.path('store')}", language)
                if game.site
                else "",
            )

        return show_store

    async def _empty(
        self,
        texts: StoreTexts,
        game: Game,
        items: list[FrontItem],
        choice: Choice,
        heroes: list[Hero],
        imported: bool,
    ) -> Empty:
        # Why nothing is listed: nothing on sale yet, a search without a match, a hero holding
        # nothing of a kind, or a kind of which nothing fits the hero, with the ways out.
        hero = choice.hero
        if not items:
            closed = "web.store.front.none." + ("empty" if imported else "closed")
            return Empty(texts.text(closed), "", "bag")
        kind = texts.text(f"games.{game.id}.item_kind.{choice.kind}") if choice.kind else ""
        everything = choice.changed(kind="", owned=False, search="").query()
        if choice.search:
            key = "web.store.front.none.query" + (".hero" if hero else "")
            name = hero.persona.name if hero else ""
            found = await self._front.matching(game, choice.search)
            links = [(texts.text("web.store.front.none.clear"), choice.changed(search="").query())]
            for other in heroes:
                if other is not hero and any(
                    item.item.id in found and _fits(item.requirement, other) for item in items
                ):
                    label = texts.text("web.store.front.none.show_for", hero=other.persona.name)
                    links.append((label, choice.changed(hero=other, owned=False).query()))
                    break
            return Empty(
                texts.text(key, query=choice.search, hero=name),
                texts.text("web.store.front.none.query.hint"),
                "find",
                links,
            )
        assert hero is not None
        name = hero.persona.name
        mine = (texts.text("web.store.front.none.all", hero=name), everything)
        if choice.owned:
            title = texts.text("web.store.front.none.owned", hero=name, kind=kind)
            return Empty(title, "", "bag", [mine])
        same = [item for item in items if item.item.kind == choice.kind]
        title = texts.text("web.store.front.none.kind", kind=kind, hero=name)
        why, links = "", [mine]
        reasons = {_unfit(item.requirement, hero) for item in same}
        if reasons == {Unfit.FACTION}:
            factions = frozenset().union(*(item.requirement.factions for item in same))
            why = texts.text(
                "web.store.front.none.faction",
                kind=kind,
                faction=texts.factions(factions),
                hero=name,
                own=texts.factions(frozenset({hero.faction or ""})),
            )
        elif reasons == {Unfit.CLASS}:
            classes = frozenset().union(*(item.requirement.classes for item in same))
            classes_named = texts.classes(classes)
            why = texts.text("web.store.front.none.class", kind=kind, classes=classes_named)
        for other in heroes:
            if other is not hero and any(_fits(item.requirement, other) for item in same):
                label = texts.text("web.store.front.none.show_for", hero=other.persona.name)
                links.insert(0, (label, choice.changed(hero=other).query()))
                break
        return Empty(title, why, kind_glyphs(game).get(choice.kind, "bag"), links)


def _choice(query: QueryParams, heroes: list[Hero], default: int | None) -> Choice:
    # The choices as the address names them; a hero the account does not have is its default.
    asked = query.get("hero", "")
    hero = next((hero for hero in heroes if str(hero.persona.id) == asked), None)
    hero = hero or next((hero for hero in heroes if hero.persona.id == default), None)
    page = query.get("page", "")
    return Choice(
        hero or (heroes[0] if heroes else None),
        query.get("kind", ""),
        query.get("owned") == "1",
        query.get("q", "").strip()[:MAX_QUERY],
        int(page) if page.isascii() and page.isdecimal() and 0 < len(page) < 6 else 1,
    )


def _kinds(game: Game, items: list[FrontItem]) -> list[str]:
    # The kinds of the items on sale, in the order of the game's files of definitions.
    present = {item.item.kind for item in items}
    order = [item_file.kind for item_file in game.catalogue.items] if game.catalogue else []
    return [kind for kind in dict.fromkeys(order) if kind in present] + sorted(present - set(order))


def kind_glyphs(game: Game) -> dict[str, str]:
    """Return the glyph of each kind of item, as the in-game store's category of the kind wears it.

    Args:
        game: The game.
    """
    store = game.store.in_game if game.store else None
    categories = store.categories if store else []
    return {kind: category.glyph for category in categories for kind in category.kinds}


def _unfit(requirement: Requirement, hero: Hero) -> Unfit | None:
    # Faction and class alone: an item that needs a higher level stays listed, as the hero grows
    # into it.
    if requirement.factions and hero.faction not in requirement.factions:
        return Unfit.FACTION
    if requirement.classes and hero.hero_class not in requirement.classes:
        return Unfit.CLASS
    return None


def _fits(requirement: Requirement, hero: Hero) -> bool:
    return _unfit(requirement, hero) is None


def _inventory(
    items: list[FrontItem], catalogue: Catalogue, held: Mapping[int, Owned]
) -> list[tuple[FrontItem | None, Item]]:
    # The items a hero holds: running low first, then those on sale in the store's order, then
    # the others by number; missions and withheld items belong to no store.
    sold = {item.item.id: item for item in items}
    order = {item_id: index for index, item_id in enumerate(sold)}
    kept = [
        item
        for item_id in held
        if (item := catalogue.item(item_id)) and item.kind != "mission" and not item.withheld
    ]
    kept.sort(key=lambda item: (not held[item.id].low, order.get(item.id, len(order)), item.id))
    return [(sold.get(item.id), item) for item in kept]


def tile(
    texts: StoreTexts,
    catalogue: Catalogue,
    found: FrontItem | None,
    item: Item,
    hero: Hero | None,
    owned: Owned | None,
    buy: str | None,
    icon: str | None,
) -> Tile:
    """Return an item's tile on a store front, for a hero or for none.

    It holds the item's name, which heroes may have it, its lowest prices and what the hero
    holds of it.

    Args:
        texts: Write in the page's language.
        catalogue: The game's catalogue, which names the item.
        found: The item as the store front lists it; none for an item the store does not sell.
        item: The item.
        hero: The chosen hero; none without one.
        owned: What the hero holds of the item; none when it holds none.
        buy: The address of the store's item pages, without the item's number; none for a tile
            that leads nowhere.
        icon: The address of the item's icon; none where the page shows OpenFESL's tile.
    """
    name = catalogue.name(item.id) or str(item.id)
    needs = requirement(texts.game, item) if found is None else found.requirement
    parts = [
        texts.factions(needs.factions) if needs.factions else "",
        texts.classes(needs.classes) if needs.classes else "",
        texts.text("web.store.front.from_level", level=needs.level) if needs.level > 1 else "",
    ]
    requires = " · ".join(part for part in parts if part) or texts.text("web.store.front.any_hero")
    line, state = "", "none"
    if hero is not None:
        if owned is not None:
            line, state = _left(texts, owned), "low" if owned.low else "held"
        elif needs.level > hero.level:
            line, state = texts.text("web.store.tile.level", level=needs.level), "level"
        else:
            line = texts.text("web.store.tile.not_owned")
    if found is None:
        unsold = "web.store.front.unsold" + (".granted" if owned and owned.granted else "")
        return Tile(name, item.kind, None, icon, requires, [], line, state, texts.text(unsold))
    prices = [
        Price(code, texts.amount(price), texts.currency(code), texts.coin(code))
        for code, price in found.prices
    ]
    href = None
    if buy is not None:
        href = f"{buy}{found.number}" + (f"?hero={hero.persona.id}" if hero else "")
    return Tile(name, item.kind, href, icon, requires, prices, line, state)


def _left(texts: StoreTexts, owned: Owned) -> str:
    # What a hero has left of an item, as the item page's hero tiles say it; a rental with
    # hours left counts a day.
    left = owned.left
    if left.unit == USES:
        said = texts.count("web.store.tile.uses", left.count)
    elif left.unit in (DAYS, HOURS):
        said = texts.count("web.store.tile.days", left.count if left.unit == DAYS else 1)
    else:
        said = texts.text("web.store.tile.permanent")
    return texts.text("web.store.front.low", left=said) if owned.low else said
