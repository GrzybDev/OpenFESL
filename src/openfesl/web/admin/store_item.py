from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from babel import Locale
from babel.lists import format_list
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.catalogues import Catalogue
from openfesl.core.games import Game
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership
from openfesl.core.store_front import front_item
from openfesl.core.store_prices import (
    ItemPrices,
    PointChange,
    PriceRefusal,
    PriceRefused,
    checked_price,
    new_point,
    reset_points,
)
from openfesl.core.store_rules import StoreRules
from openfesl.core.texts import translate
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import (
    SECTION,
    StoreAdminViews,
    ordered_lines,
    price_row,
    store_url,
)
from openfesl.web.pages import whole_number
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_front import Tile, kind_glyphs, tile
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class Adding:
    """What the panel that adds a price point to an item offers.

    Attributes:
        currencies: The currencies a price may be in.
        uses: Whether the item is sold in uses rather than for days or for good.
        ownerships: The ways to own it otherwise, as game data writes them, such as 7d.
    """

    currencies: list[str]
    uses: bool
    ownerships: list[str]


@dataclass(frozen=True, slots=True)
class Change:
    """A change the admin sent of an item's price points, before it is saved.

    Attributes:
        admin: The admin.
        game: The item's game.
        rules: How the game's store prices items.
        catalogue: The game's catalogue, in the page's language.
        prices: The item with its price points.
        form: The form's fields.
    """

    admin: Admin
    game: Game
    rules: StoreRules
    catalogue: Catalogue
    prices: ItemPrices
    form: dict[str, str]

    @property
    def name(self) -> str:
        """The item's name."""
        return self.catalogue.name(self.prices.item.id) or str(self.prices.item.id)


def store_item_routes(sessions: AdminSessions, views: StoreAdminViews) -> APIRouter:
    """Return each item's prices in its game's store, which admins change, add to and reset.

    Every role reads them; changing them needs the admin role. Nothing is saved while one of
    them is refused, and every change is recorded among the admin's actions.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and save the price points.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/items/{item_id}"

    async def page(
        request: Request,
        admin: Admin,
        game: Game,
        item_id: int,
        form: Mapping[str, str] | None = None,
        refused: PriceRefused | None = None,
    ) -> HTMLResponse:
        rules = await views.rules(game)
        language = request.state.language
        catalogue, prices = await views.item(game, item_id, language)
        texts = StoreTexts(game, language, datetime.now(UTC))
        name = catalogue.name(item_id) or str(item_id)
        # The preview follows the prices that a script sends as they are typed, or a refused form.
        drafted = form or request.query_params
        return await sessions.render(
            request,
            "admin.StoreItem",
            400 if refused else 200,
            admin=admin,
            of=game,
            catalogue=catalogue,
            row=price_row(game, texts, name, prices),
            lines=ordered_lines(game, prices.lines),
            rule=_rule_text(texts, rules, prices),
            manages=admin.role is Role.ADMIN,
            adding=_adding(game, rules, prices),
            form=dict(form or {}),
            refused=refused.refusal.value if refused else "",
            refused_point=refused.product if refused else None,
            why=_why(texts, rules, name, form or {}, refused) if refused else "",
            preview=None if prices.fixed else _preview(texts, catalogue, prices, drafted),
            glyph=kind_glyphs(game).get(prices.item.kind, "bag"),
        )

    async def change(request: Request, game_id: str, item_id: int) -> Change:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.game(game_id)
        rules = await views.rules(game)
        form = await request.form()
        check_form(request, form)
        catalogue, prices = await views.item(game, item_id, request.state.language)
        if prices.fixed or prices.unsold:
            raise HTTPException(400)
        fields = {key: str(value) for key, value in form.items()}
        return Change(admin, game, rules, catalogue, prices, fields)

    async def saved(
        request: Request, change: Change, points: list[PointChange], key: str, point: str = ""
    ) -> Response:
        item_id = change.prices.item.id
        if await views.save(change.game, item_id, points, key == "reset", change.admin):
            toast(request, f"web.admin.toast.store.{key}", item=change.name, point=point)
        else:
            toast(request, "web.admin.toast.store.unchanged", item=change.name)
        return RedirectResponse(store_url(request, change.game, f"/items/{item_id}"), 303)

    @router.get(path, response_class=HTMLResponse)
    async def item(request: Request, game_id: str, item_id: int) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        return await page(request, admin, game, item_id)

    @router.post(path)
    async def save(request: Request, game_id: str, item_id: int) -> Response:
        sent = await change(request, game_id, item_id)
        unsold = bool(sent.form.get("unsold"))
        points = []
        try:
            for line in sent.prices.lines:
                product = line.product
                price = _number(sent.form.get(f"price-{product.id}", ""), product.id)
                published = not unsold and bool(sent.form.get(f"sold-{product.id}"))
                points.append(
                    PointChange(
                        product.id,
                        product.item_id,
                        product.ownership,
                        product.currency,
                        checked_price(price, product.id),
                        published,
                    )
                )
        except PriceRefused as refused:
            return await page(request, sent.admin, sent.game, item_id, sent.form, refused)
        return await saved(request, sent, points, "saved")

    @router.post(path + "/add")
    async def add(request: Request, game_id: str, item_id: int) -> Response:
        sent = await change(request, game_id, item_id)
        adding = _adding(sent.game, sent.rules, sent.prices)
        currency = sent.form.get("add-currency", "")
        if currency not in adding.currencies:
            raise HTTPException(400)
        entered = sent.form.get("add-price", "").strip()
        try:
            if adding.uses:
                uses = whole_number(sent.form.get("add-uses", ""), MOST_BALANCE)
                if uses is None:
                    raise PriceRefused(PriceRefusal.USES)
                ownership = Ownership(COUNTED, uses=uses)
            else:
                ownership = _ownership(sent.form.get("add-ownership", ""))
            price = _number(entered) if entered else None
            point = new_point(sent.rules, sent.prices, currency, ownership, price)
        except PriceRefused as refused:
            return await page(request, sent.admin, sent.game, item_id, sent.form, refused)
        texts = StoreTexts(sent.game, request.state.language, datetime.now(UTC))
        named = _point(texts, point.currency, point.ownership)
        return await saved(request, sent, [point], "added", named)

    @router.post(path + "/reset")
    async def reset(request: Request, game_id: str, item_id: int) -> Response:
        sent = await change(request, game_id, item_id)
        return await saved(request, sent, reset_points(sent.rules, sent.prices), "reset")

    return router


def _number(text: str, product: int | None = None) -> int:
    # A whole number a field holds; another is refused as no price.
    number = whole_number(text, MOST_BALANCE, 0)
    if number is None:
        raise PriceRefused(PriceRefusal.PRICE, product)
    return number


def _preview(
    texts: StoreTexts, catalogue: Catalogue, prices: ItemPrices, form: Mapping[str, str]
) -> Tile:
    # The item's tile as the store front lists it, at the prices a form holds where it sends
    # them, else at those saved; prices in the heroes' points are the game's menu's alone.
    heroes = texts.game.heroes
    drafted = any(key.startswith("price-") for key in form)
    sold = []
    for line in prices.lines:
        product = line.product
        if drafted:
            try:
                price = _number(form.get(f"price-{product.id}", ""))
            except PriceRefused:
                continue
            if form.get("unsold") or not form.get(f"sold-{product.id}") or not price:
                continue
            product = replace(product, price=price)
        if (drafted or product.published) and (not heroes or product.currency != heroes.points):
            sold.append(product)
    item = prices.item
    icon = (
        f"/admin/icons/{texts.game.id}/{item.id}"
        if item.icon and catalogue.contents.icons
        else None
    )
    if not sold:
        hidden = tile(texts, catalogue, None, item, None, None, None, icon)
        return replace(hidden, unsold=texts.text("web.admin.store.preview.hidden"))
    return tile(texts, catalogue, front_item(texts.game, item, sold), item, None, None, None, icon)


def _ownership(written: str) -> Ownership:
    # The rental or ownership for good that a tile of the add panel names; days that the rules
    # know no share of are refused by the point's check.
    days = written.removesuffix("d")
    if written == "perm":
        return Ownership(PERMANENT)
    if written.endswith("d") and days.isascii() and days.isdecimal() and len(days) < 6:
        return Ownership(RENTED, days=int(days))
    raise PriceRefused(PriceRefusal.DURATION)


def _adding(game: Game, rules: StoreRules, prices: ItemPrices) -> Adding:
    points = game.heroes.points if game.heroes else None
    priced = {rule.currency for rule in rules.prices.values()}
    currencies = [code for code in game.currencies if code in priced and code != points]
    uses = rules.counted(prices.item)
    return Adding(currencies, uses, [f"{days}d" for days in sorted(rules.rentals)] + ["perm"])


def _point(texts: StoreTexts, currency: str, ownership: Ownership) -> str:
    # Such as "Valor Points for 1 day".
    return texts.text(
        "web.admin.store.point.named",
        currency=texts.currency(currency),
        ownership=texts.ownership(ownership, inline=True),
    )


def _why(
    texts: StoreTexts,
    rules: StoreRules,
    name: str,
    form: Mapping[str, str],
    refused: PriceRefused,
) -> str:
    # The refusal in words, beside the point's row or in the add panel.
    match refused.refusal:
        case PriceRefusal.PRICE:
            field = f"price-{refused.product}" if refused.product else "add-price"
            return texts.text(
                "web.admin.store.refused.price",
                price=form.get(field, "").strip() or "0",
                most=texts.amount(MOST_BALANCE),
            )
        case PriceRefusal.TWICE:
            count = whole_number(form.get("add-uses", ""), MOST_BALANCE)
            ownership = (
                Ownership(COUNTED, uses=count)
                if count
                else _ownership(form.get("add-ownership", ""))
            )
            point = _point(texts, form.get("add-currency", ""), ownership)
            return texts.text("web.admin.store.refused.twice", point=point, item=name)
        case PriceRefusal.DURATION:
            locale = Locale.parse(translate("web.locale", texts.language))
            known = [texts.amount(days) for days in sorted(rules.rentals)]
            written = form.get("add-ownership", "")
            days = written.removesuffix("d")
            duration = (
                texts.count("web.store.price.days", int(days))
                if days.isascii() and days.isdecimal() and len(days) < 6
                else written
            )
            return texts.text(
                "web.admin.store.refused.duration",
                duration=duration,
                days=format_list(known, locale=locale),
            )
        case PriceRefusal.USES:
            uses = form.get("add-uses", "").strip() or "0"
            return texts.text("web.admin.store.refused.uses", uses=uses)


def _rule_text(texts: StoreTexts, rules: StoreRules, prices: ItemPrices) -> str:
    # What each rule that prices the item makes owning it for good worth, or a use of it.
    said = []
    for rule in rules.rules(prices.item):
        if value := rules.price(rule, Ownership(PERMANENT)):
            said.append(texts.priced(value, rule.currency))
        elif rule.use_price:
            price = texts.priced(rule.use_price, rule.currency)
            said.append(texts.text("web.admin.store.rule.use", price=price))
        elif rule.points:
            said.append(texts.text("web.admin.store.rule.listed"))
    return " · ".join(said) or texts.text("web.admin.store.rule.none")
