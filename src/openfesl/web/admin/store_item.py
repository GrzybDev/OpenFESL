from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.admins import Role
from openfesl.core.store_prices import ItemPrices
from openfesl.core.store_rules import StoreRules
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import StoreAdminViews, ordered_lines, price_row
from openfesl.web.store_texts import StoreTexts


def store_item_routes(sessions: AdminSessions, views: StoreAdminViews) -> APIRouter:
    """Return each item's prices in its game's store: its rule and its price points.

    Every role reads them.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show.
    """
    router = APIRouter()

    @router.get("/admin/store/{game_id}/items/{item_id}", response_class=HTMLResponse)
    async def item(request: Request, game_id: str, item_id: int) -> HTMLResponse:
        admin = sessions.admin(request)
        game, rules = views.game(game_id)
        language = request.state.language
        catalogue, prices = await views.item(game, item_id, language)
        texts = StoreTexts(game, language, datetime.now(UTC))
        name = catalogue.name(item_id) or str(item_id)
        return await sessions.render(
            request,
            "admin.StoreItem",
            admin=admin,
            of=game,
            catalogue=catalogue,
            row=price_row(game, texts, name, prices),
            lines=ordered_lines(game, prices.lines),
            rule=_rule_text(texts, rules, prices),
            manages=admin.role is Role.ADMIN,
        )

    return router


def _rule_text(texts: StoreTexts, rules: StoreRules, prices: ItemPrices) -> str:
    # What each rule that prices the item makes owning it for good worth, or a use of it.
    said = []
    for rule in rules.rules(prices.item):
        if rule.value:
            said.append(texts.priced(rule.value, rule.currency))
        elif rule.use_price:
            price = texts.priced(rule.use_price, rule.currency)
            said.append(texts.text("web.admin.store.rule.use", price=price))
    return " · ".join(said) or texts.text("web.admin.store.rule.none")
