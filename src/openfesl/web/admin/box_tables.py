from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.box_tables import TableEntry, TableRefusal, TableRefused, check_table
from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.drops import Box
from openfesl.core.games import Game
from openfesl.core.inventory import Ownership
from openfesl.core.store import Product
from openfesl.core.store_prices import ItemPrices
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.boxes import BoxAdminViews, BoxRow, Entry, tiers
from openfesl.web.admin.store import SECTION, store_url
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

# How many items a search of the add panel lists at most.
MATCHES = 8


@dataclass(frozen=True, slots=True)
class Option:
    """A way of owning an item that the add panel offers, with its value in the box's currency.

    Attributes:
        written: The ownership as game data writes it, such as 7d.
        price: Its value.
        label: Both in words, such as 7 days · 672.
    """

    written: str
    price: int
    label: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """An item that a search found to add to a box's drop table.

    Attributes:
        item: The item.
        name: Its name.
        options: The ways a hero may own it, with their value in the box's currency.
    """

    item: Item
    name: str
    options: list[Option]


def box_table_routes(sessions: AdminSessions, views: BoxAdminViews) -> APIRouter:
    """Return each box's drop table, which admins change, add entries to and take entries from.

    Every role reads it; changing it needs the admin role. Nothing is saved while one of the
    table's rules is broken, and every change is recorded among the admin's actions.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and save the tables.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/boxes/{box_id}"

    async def found(request: Request, game_id: str, box_id: int) -> tuple[Game, Catalogue, BoxRow]:
        game = views.stores.game(game_id)
        catalogue, rows = await views.rows(game, request.state.language)
        row = next((row for row in rows if row.box.id == box_id), None)
        if row is None:
            raise HTTPException(404)
        return game, catalogue, row

    async def page(
        request: Request,
        admin: Admin,
        game_id: str,
        box_id: int,
        form: Mapping[str, str] | None = None,
        refused: str = "",
    ) -> HTMLResponse:
        game, catalogue, row = await found(request, game_id, box_id)
        fallback = row.box.fallback
        query = request.query_params.get("q", "").strip()[:64]
        return await sessions.render(
            request,
            "admin.StoreBox",
            400 if refused else 200,
            admin=admin,
            of=game,
            catalogue=catalogue,
            row=row,
            tiers=tiers(row.box, catalogue),
            fallback=Entry(
                fallback, catalogue.name(fallback.item_id) or str(fallback.item_id), True
            )
            if fallback
            else None,
            manages=admin.role is Role.ADMIN,
            query=query,
            candidates=await _candidates(views, game, catalogue, row, query, request)
            if query
            else [],
            form=dict(form or {}),
            refused=refused,
        )

    async def saved(
        request: Request,
        admin: Admin,
        game: Game,
        catalogue: Catalogue,
        row: BoxRow,
        weights: list[int],
        entries: list[TableEntry],
        worth_rule: bool,
        form: Mapping[str, str],
    ) -> Response:
        box = row.box
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        try:
            check_table(box, weights, entries, worth_rule, row.promised)
        except TableRefused as refused:
            why = _why(texts, catalogue, row, weights, refused)
            return await page(request, admin, game.id, box.id, form, why)
        before = {(drop.product.item_id, drop.product.ownership.written) for drop in box.drops}
        after = {(entry.item_id, entry.ownership.written) for entry in entries}
        details: dict[str, Any] = {
            "game": game.id,
            "box": box.key,
            "weights": [_weights(box), weights],
            "removed": sorted(before - after),
            "added": sorted(after - before),
            "worth": [box.worth_rule, worth_rule],
        }
        if (
            details["removed"]
            or details["added"]
            or _weights(box) != weights
            or (box.worth_rule != worth_rule)
        ):
            await views.save_table(box, weights, entries, worth_rule, details, admin)
            toast(request, "web.admin.toast.boxes.saved", box=row.name)
        else:
            toast(request, "web.admin.toast.boxes.unchanged", box=row.name)
        return RedirectResponse(store_url(request, game, f"/boxes/{box.id}"), 303)

    @router.get(path, response_class=HTMLResponse)
    async def box_page(request: Request, game_id: str, box_id: int) -> HTMLResponse:
        return await page(request, sessions.admin(request), game_id, box_id)

    @router.post(path)
    async def save(request: Request, game_id: str, box_id: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        fields = {key: str(value) for key, value in form.items()}
        game, catalogue, row = await found(request, game_id, box_id)
        box = row.box
        # Weights in whole percent; any other text counts as none.
        written = [
            fields.get(f"weight-{tier}", "").strip().removesuffix("%").strip()
            for tier in range(_tiers_of(row) + 1)
        ]
        weights = [
            int(digits) if digits.isascii() and digits.isdecimal() and len(digits) < 4 else 0
            for digits in written
        ]
        entries = [
            _entry(drop.tier, drop.product, box.currency)
            for drop in box.drops
            if not fields.get(f"remove-{drop.product.id}")
        ]
        worth_rule = bool(fields.get("worth"))
        return await saved(
            request, admin, game, catalogue, row, weights, entries, worth_rule, fields
        )

    @router.post(path + "/add")
    async def add(request: Request, game_id: str, box_id: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        fields = {key: str(value) for key, value in form.items()}
        game, catalogue, row = await found(request, game_id, box_id)
        box = row.box
        item_id = fields.get("item", "")
        tier = fields.get("tier", "")
        candidates = await _candidates(views, game, catalogue, row, item_id, request)
        chosen = next((found for found in candidates if str(found.item.id) == item_id), None)
        written = fields.get("ownership")
        option = next((o for o in chosen.options if o.written == written), None) if chosen else None
        if chosen is None or option is None or not tier.isdecimal() or int(tier) > _tiers_of(row):
            raise HTTPException(400)
        ownership = Ownership.read(option.written)
        assert isinstance(ownership, Ownership)
        entries = [_entry(drop.tier, drop.product, box.currency) for drop in box.drops]
        entries.append(TableEntry(int(tier), chosen.item.id, ownership, option.price, None))
        return await saved(
            request, admin, game, catalogue, row, _weights(box), entries, box.worth_rule, fields
        )

    return router


def _tiers_of(row: BoxRow) -> int:
    # The number of the box's last tier.
    return max((drop.tier for drop in row.box.drops), default=0)


def _weights(box: Box) -> list[int]:
    # The weight of each tier of a box, by its number; 0 for a number no tier has.
    weights = [0] * (max((drop.tier for drop in box.drops), default=0) + 1)
    for drop in box.drops:
        weights[drop.tier] = drop.weight
    return weights


def _entry(tier: int, product: Product, currency: str) -> TableEntry:
    # An entry the table keeps, valued in the box's currency.
    price = product.price if product.currency == currency else None
    return TableEntry(tier, product.item_id, product.ownership, price, product.id)


async def _candidates(
    views: BoxAdminViews,
    game: Game,
    catalogue: Catalogue,
    row: BoxRow,
    query: str,
    request: Request,
) -> list[Candidate]:
    # The sellable items whose name holds the search, or whose number it is, with the ways a
    # hero may own each and their value in the box's currency.
    rules = await views.stores.rules(game)
    _, prices = await views.stores.prices(game, request.state.language)
    texts = StoreTexts(game, request.state.language, datetime.now(UTC))
    kinds = {rule.kind for rule in rules.prices.values()}
    held: dict[int, ItemPrices] = {}
    for item_prices in prices:
        for number in item_prices.item.levels or (item_prices.item.id,):
            held[number] = item_prices
    folded = query.casefold()
    found = []
    for item in sorted(catalogue.contents.items.values(), key=lambda item: item.id):
        name = catalogue.name(item.id)
        if not name or not rules.sellable(item) or item.kind not in kinds:
            continue
        if str(item.id) != query and folded not in name.casefold():
            continue
        options: dict[Ownership, int] = {}
        for line in held[item.id].lines if item.id in held else ():
            product = line.product
            if product.item_id == item.id and product.currency == row.box.currency:
                options[product.ownership] = product.price
        for rule in rules.rules(item):
            if rule.currency == row.box.currency:
                for ownership in rule.sold:
                    if (price := rules.price(rule, ownership)) and ownership not in options:
                        options[ownership] = price
        offered = [
            Option(owned.written, price, f"{texts.ownership(owned)} · {texts.amount(price)}")
            for owned, price in sorted(options.items(), key=lambda pair: pair[0].written)
        ]
        if offered:
            found.append(Candidate(item, name, offered))
        if len(found) == MATCHES:
            break
    return found


def _why(
    texts: StoreTexts,
    catalogue: Catalogue,
    row: BoxRow,
    weights: list[int],
    refused: TableRefused,
) -> str:
    # The refusal in words.
    entry = refused.entry
    match refused.refusal:
        case TableRefusal.WEIGHTS:
            return texts.text("web.admin.boxes.refused.weights", sum=texts.amount(sum(weights)))
        case TableRefusal.EMPTY:
            tier = texts.text("web.admin.boxes.tier", number=(refused.tier or 0) + 1)
            return texts.text("web.admin.boxes.refused.empty", tier=tier)
        case TableRefusal.PROMISED:
            return texts.text("web.admin.boxes.refused.promised", box=row.name)
    assert entry is not None
    item = catalogue.name(entry.item_id) or str(entry.item_id)
    if refused.refusal is TableRefusal.NO_PRICE:
        currency = texts.currency(row.box.currency)
        return texts.text("web.admin.boxes.refused.no_price", item=item, currency=currency)
    return texts.text(
        "web.admin.boxes.refused.below",
        item=item,
        ownership=texts.ownership(entry.ownership, inline=True),
        value=texts.priced(entry.price or 0, row.box.currency),
        price=texts.amount(row.box.value),
    )
