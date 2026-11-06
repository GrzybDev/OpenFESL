from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import ceil
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException

from openfesl.core.account_rules import account_rules
from openfesl.core.admins import Admin
from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED
from openfesl.core.policies import SettingStore
from openfesl.core.store import store_rules
from openfesl.core.store_items import requirement
from openfesl.core.store_prices import (
    ItemPrices,
    PointChange,
    PriceLine,
    PriceState,
    PriceStore,
    item_prices,
)
from openfesl.core.store_rules import StoreRules
from openfesl.core.texts import localize, translate_count
from openfesl.web.admin.auth import AdminSessions, game_among
from openfesl.web.store_front import kind_glyphs
from openfesl.web.store_texts import StoreTexts

SECTION = "store"
PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class ItemList:
    """Which items the store's prices list, as its address says; what is empty does not filter.

    Attributes:
        kind: The items' kind.
        faction: A faction whose heroes may have them.
        hero_class: A class whose heroes may have them.
        states: How the store sells them; every way when empty.
        search: The start of their name, or an item's number.
        page: The page of the list, from 1.
    """

    kind: str = ""
    faction: str = ""
    hero_class: str = ""
    states: tuple[PriceState, ...] = ()
    search: str = ""
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        page = query.get("page", "")
        return cls(
            kind=query.get("kind", ""),
            faction=query.get("faction", ""),
            hero_class=query.get("class", ""),
            states=tuple(state for state in PriceState if query.get(state.value)),
            search=query.get("q", "").strip()[:64],
            page=int(page) if page.isdigit() and 0 < int(page) < 10**6 else 1,
        )

    def changed(self, **changes: Any) -> ItemList:
        """Return the list with some choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        fields = [
            ("kind", self.kind),
            ("faction", self.faction),
            ("class", self.hero_class),
            *((state.value, "1") for state in self.states),
            ("q", self.search),
            ("page", str(self.page) if self.page > 1 else ""),
        ]
        query = urlencode([(key, value) for key, value in fields if value])
        return f"?{query}" if query else ""


@dataclass(frozen=True, slots=True)
class PriceRow:
    """An item as the store's prices list it.

    Attributes:
        prices: The item with its price points.
        name: Its name.
        requires: Which heroes may have it.
        summary: How many price points it has, or why it is never sold.
        ways: How it is sold, by currency.
    """

    prices: ItemPrices
    name: str
    requires: str
    summary: str
    ways: str


@dataclass(frozen=True, slots=True)
class PriceList:
    """The store's prices of a game, as a page of its list shows them.

    Attributes:
        game: The game.
        catalogue: Its imported catalogue, in the page's language.
        listing: Which items the list shows.
        rows: The items of the page.
        total: How many items the list's filter lets through.
        kinds: How many items of each kind the store prices, by kind, in the rules' order.
        states: How many items it sells each way, by state.
    """

    game: Game
    catalogue: Catalogue
    listing: ItemList
    rows: list[PriceRow]
    total: int
    kinds: dict[str, int]
    states: dict[PriceState, int]


class StoreAdminViews:
    """Puts together what the admin panel shows of the games' stores."""

    def __init__(
        self,
        games: Games,
        catalogues: Catalogues,
        prices: PriceStore,
        settings: SettingStore,
        apply_actions: Callable[[], Awaitable[None]],
    ) -> None:
        """Read the stores' price points and the catalogues whose items they sell.

        Args:
            games: The games, of which those with store rules have a store.
            catalogues: The games' imported catalogues.
            prices: Where the stores' price points are kept.
            settings: The instance's settings, which override the price rules of the games' data.
            apply_actions: Hands the admin's recorded actions to the running server, whose
                games' lists of products then show the new prices and rules at once.
        """
        self.games = [game for game in games if game.store and game.heroes]
        self._catalogues = catalogues
        self.store = prices
        self._settings = settings
        self._apply_actions = apply_actions

    def game(self, game_id: str) -> Game:
        """Return a game with a store.

        Args:
            game_id: The game's data-file name.

        Raises:
            HTTPException: 404 when no game with a store has the name.
        """
        return game_among(self.games, game_id)

    async def rules(self, game: Game) -> StoreRules:
        """Return how a game's store prices items, with the instance's settings over its data.

        Args:
            game: The game, which has a store.
        """
        rules = await store_rules(game, self._settings)
        assert rules is not None
        return rules

    async def prices(self, game: Game, language: str) -> tuple[Catalogue, list[ItemPrices]]:
        """Return a game's catalogue and every item its store prices, with its price points.

        Args:
            game: The game, which has a store.
            language: The language of the items' names.
        """
        rules = await self.rules(game)
        catalogue = await self._catalogues.of(game, language)
        products = await self.store.every(game.id)
        return catalogue, item_prices(game, rules, catalogue, products)

    async def save(
        self, game: Game, item_id: int, points: list[PointChange], reset: bool, admin: Admin
    ) -> bool:
        """Save an item's price points as the admin changed them, and record the change.

        Args:
            game: The game, which has a store.
            item_id: The item's number, an ability's first level for all of its levels.
            points: Its price points, those to add among them.
            reset: Whether they are its rule's, as the admin reset them.
            admin: The admin who saved them.

        Returns:
            Whether anything changed.

        Raises:
            PriceRefused: When a point to add is one the store has, also at the same time.
        """
        changed = await self.store.save_points(game.id, item_id, points, reset, admin)
        await self._apply_actions()
        return changed

    async def account_rules(self, game: Game) -> Game:
        """Return a game with the account prices, allowances and per-round caps the instance sets.

        Args:
            game: The game, which has a store.
        """
        return await account_rules(game, self._settings)

    async def apply_rule(
        self,
        game: Game,
        settings: Mapping[str, str | None],
        points: list[PointChange],
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Change a price rule and the price points it gives, and record it as one action.

        Args:
            game: The game, which has a store.
            settings: The settings that override the rule's fields, by key; None removes one.
            points: The price points the rule changes, those to add among them.
            details: What the admin's log keeps of the change: the rule and what it changed.
            admin: The admin who applied it.
        """
        await self.store.apply_rule(game.id, settings, points, details, admin)
        await self._apply_actions()

    async def item(self, game: Game, item_id: int, language: str) -> tuple[Catalogue, ItemPrices]:
        """Return a game's catalogue and one item its store prices, with its price points.

        Args:
            game: The game, which has a store.
            item_id: The item's number, an ability's first level for all of its levels.
            language: The language of the item's name.

        Raises:
            HTTPException: 404 when the store prices no such item.
        """
        catalogue, found = await self.prices(game, language)
        prices = next((prices for prices in found if prices.item.id == item_id), None)
        if prices is None:
            raise HTTPException(404)
        return catalogue, prices

    async def listed(self, game: Game, listing: ItemList, language: str) -> PriceList:
        """Return a page of a game's store prices.

        Args:
            game: The game, which has a store.
            listing: Which items the list shows.
            language: The page's language.
        """
        catalogue, found = await self.prices(game, language)
        texts = StoreTexts(game, language, datetime.now(UTC))
        named = sorted(
            ((catalogue.name(prices.item.id) or "", prices) for prices in found),
            key=lambda pair: (pair[0].casefold(), pair[1].item.id),
        )
        search = listing.search.casefold()
        chosen = [
            (name, prices)
            for name, prices in named
            if (not listing.kind or prices.item.kind == listing.kind)
            and (not listing.faction or listing.faction in prices.item.factions)
            and (not listing.hero_class or listing.hero_class in prices.item.classes)
            and (not listing.states or prices.state in listing.states)
            and (
                not search
                or name.casefold().startswith(search)
                or search in {str(number) for number in prices.item.levels or (prices.item.id,)}
            )
        ]
        start = (listing.page - 1) * PAGE_SIZE
        shown = chosen[start : start + PAGE_SIZE]
        rows = [price_row(game, texts, name, prices) for name, prices in shown]
        rules = (await self.rules(game)).prices.values()
        order = list(dict.fromkeys(rule.kind for rule in rules))
        counted = Counter(prices.item.kind for prices in found)
        return PriceList(
            game,
            catalogue,
            listing,
            rows,
            len(chosen),
            {kind: counted[kind] for kind in order},
            {state: sum(prices.state is state for prices in found) for state in PriceState},
        )


def store_routes(sessions: AdminSessions, views: StoreAdminViews) -> APIRouter:
    """Return the store's prices: every item of a game's catalogue with its price points.

    Every role reads them.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show.
    """
    router = APIRouter()

    @router.get("/admin/store")
    async def first(request: Request) -> Response:
        sessions.admin(request)
        if not views.games:
            raise HTTPException(404)
        return RedirectResponse(store_url(request, views.games[0]), 303)

    @router.get("/admin/store/{game_id}", response_class=HTMLResponse)
    async def prices(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        listing = ItemList.read(request.query_params)
        found = await views.listed(game, listing, request.state.language)
        return await sessions.render(
            request,
            "admin.StorePrices",
            admin=admin,
            found=found,
            glyphs=kind_glyphs(game),
            pages=ceil(found.total / PAGE_SIZE),
            size=PAGE_SIZE,
        )

    return router


def store_url(request: Request, game: Game, path: str = "") -> str:
    """Return the address of a page of a game's store in the admin panel, in the page's language.

    Args:
        request: The request, whose language the address takes.
        game: The game.
        path: The page's path below the store's, such as /rules.
    """
    return localize(f"/admin/store/{game.id}{path}", request.state.language)


def ordered_lines(game: Game, lines: tuple[PriceLine, ...]) -> list[PriceLine]:
    """Return an item's price points in the order its page lists them.

    By level, then by currency in the game's order, then rentals and uses from the shortest,
    then for good.

    Args:
        game: The game.
        lines: The price points.
    """
    order = list(game.currencies)
    return sorted(
        lines,
        key=lambda line: (
            line.product.item_id,
            order.index(line.product.currency) if line.product.currency in order else len(order),
            line.product.ownership.type == PERMANENT,
            line.product.ownership.days or line.product.ownership.uses or 0,
        ),
    )


def price_row(game: Game, texts: StoreTexts, name: str, prices: ItemPrices) -> PriceRow:
    """Return an item as the store's prices list it.

    Args:
        game: The game.
        texts: Write in the page's language.
        name: The item's name.
        prices: The item with its price points.
    """
    item = prices.item
    requires = texts.requires(requirement(game, item))
    sold = [line for line in ordered_lines(game, prices.lines) if line.product.published]
    if prices.fixed:
        steps = len(item.levels) or 1
        return PriceRow(
            prices,
            name,
            requires,
            texts.count("web.admin.store.steps", steps),
            texts.text("web.admin.store.fixed.ways", currency=texts.currency(points))
            if (points := game.heroes.points if game.heroes else "")
            else "",
        )
    if prices.unsold:
        return PriceRow(
            prices,
            name,
            requires,
            texts.text("web.admin.store.points.none"),
            texts.text(f"web.admin.store.unsold.{prices.unsold.value}"),
        )
    return PriceRow(
        prices,
        name,
        requires,
        texts.count("web.admin.store.points.count", len(sold))
        if sold
        else texts.text("web.admin.store.points.none"),
        _ways(game, texts, sold),
    )


def _ways(game: Game, texts: StoreTexts, lines: list[PriceLine]) -> str:
    # Such as "Valor Points 1, 3, 7 days · Funds 30 days, permanent".
    said = []
    for code in dict.fromkeys(line.product.currency for line in lines):
        owned = [line.product.ownership for line in lines if line.product.currency == code]
        parts = []
        for kind, key in ((RENTED, "days"), (COUNTED, "uses")):
            numbers = [(o.days if kind == RENTED else o.uses) or 0 for o in owned if o.type == kind]
            if numbers:
                joined = ", ".join(texts.amount(value) for value in numbers)
                text = translate_count(f"web.admin.store.ways.{key}", numbers[-1], texts.language)
                parts.append(text % {"numbers": joined})
        if any(o.type == PERMANENT for o in owned):
            parts.append(texts.text("web.admin.store.ways.permanent"))
        said.append(
            texts.text("web.admin.store.ways", currency=texts.currency(code), ways=", ".join(parts))
        )
    return " · ".join(said)
