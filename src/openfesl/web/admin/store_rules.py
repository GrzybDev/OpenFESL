import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.catalogues import Catalogue
from openfesl.core.games import Game
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership
from openfesl.core.store_prices import ItemPrices, applied
from openfesl.core.store_rules import PriceRule, StoreRules
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import SECTION, StoreAdminViews, store_url
from openfesl.web.pages import whole_number
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class RuleRow:
    """A price rule of a game's store as its kind rules list it.

    Attributes:
        key: Its key in the game's data.
        rule: The rule as the instance sets it.
        seed: The rule as the game's data gives it.
        items: How many items it prices.
        own: How many of them have prices of their own in its currency.
        fixed: Whether it prices in the heroes' points, which no admin changes.
    """

    key: str
    rule: PriceRule
    seed: PriceRule
    items: int
    own: int
    fixed: bool


@dataclass(frozen=True, slots=True)
class RuleChange:
    """A rule as the admin set it in its form, before it is applied.

    Attributes:
        rule: The rule with the new values.
        fields: The form's fields that carry them to the confirmation.
        refused: Why the values are not taken, in words; empty when they are.
    """

    rule: PriceRule
    fields: dict[str, str]
    refused: str = ""


def rule_rows(
    data: StoreRules, rules: StoreRules, found: list[ItemPrices], points: str | None
) -> list[RuleRow]:
    """Return the price rules of a game's store with how many items each prices.

    Args:
        data: The rules as the game's data gives them.
        rules: The rules as the instance sets them.
        found: The items of the game's catalogue with their price points.
        points: The currency of the heroes' points, whose prices are fixed.
    """
    keys = {id(rule): key for key, rule in rules.prices.items()}
    items = dict.fromkeys(rules.prices, 0)
    own = dict.fromkeys(rules.prices, 0)
    for prices in found:
        if prices.unsold:
            continue
        for rule in rules.rules(prices.item):
            key = keys[id(rule)]
            items[key] += 1
            lines = [line for line in prices.lines if line.product.currency == rule.currency]
            own[key] += any(line.own for line in lines)
    return [
        RuleRow(key, rule, data.prices[key], items[key], own[key], rule.currency == points)
        for key, rule in rules.prices.items()
    ]


def store_rule_routes(sessions: AdminSessions, views: StoreAdminViews) -> APIRouter:
    """Return the price rules of each game's store, which admins change and apply.

    Every role reads them; changing them needs the admin role. A changed rule is applied after
    a confirmation that says how many items take its prices and how many keep their own.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and apply the rules.
    """
    router = APIRouter()

    async def found(game: Game, language: str) -> tuple[StoreRules, Catalogue, list[ItemPrices]]:
        rules = await views.rules(game)
        catalogue, items = await views.prices(game, language)
        return rules, catalogue, items

    async def page(
        request: Request, admin: Admin, game: Game, key: str, change: RuleChange | None = None
    ) -> HTMLResponse:
        rules, _, items = await found(game, request.state.language)
        assert game.store is not None
        rows = rule_rows(game.store, rules, items, game.heroes.points if game.heroes else None)
        chosen = next((row for row in rows if row.key == key and not row.fixed), None)
        chosen = chosen or next(row for row in rows if not row.fixed)
        return await sessions.render(
            request,
            "admin.StoreRules",
            400 if change else 200,
            admin=admin,
            of=game,
            rules=rules,
            rows=rows,
            chosen=chosen,
            form=change.fields if change else {},
            why=change.refused if change else "",
            manages=admin.role is Role.ADMIN,
        )

    async def changed(
        request: Request, game_id: str, key: str
    ) -> tuple[Admin, Game, StoreRules, RuleChange]:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.game(game_id)
        form = await request.form()
        check_form(request, form)
        rules = await views.rules(game)
        rule = rules.prices.get(key)
        if rule is None or (game.heroes and rule.currency == game.heroes.points):
            raise HTTPException(404)
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        return admin, game, rules, _change(texts, rules, rule, form)

    @router.get("/admin/store/{game_id}/rules", response_class=HTMLResponse)
    async def rules_page(request: Request, game_id: str, rule: str = "") -> HTMLResponse:
        admin = sessions.admin(request)
        return await page(request, admin, views.game(game_id), rule)

    @router.post("/admin/store/{game_id}/rules/{key}", response_class=HTMLResponse)
    async def review(request: Request, game_id: str, key: str) -> HTMLResponse:
        admin, game, rules, change = await changed(request, game_id, key)
        if change.refused:
            return await page(request, admin, game, key, change)
        _, catalogue, items = await found(game, request.state.language)
        new = rules.model_copy(update={"prices": rules.prices | {key: change.rule}})
        outcome = applied(rules, new, key, items)
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        example = ""
        if outcome.example:
            item, points = outcome.example
            sold = " · ".join(
                _priced(texts, point.currency, point.ownership, point.price)
                for point in sorted(points, key=lambda point: _order(point.ownership))
                if point.published
            )
            name = catalogue.name(item.id) or str(item.id)
            none = texts.text("web.admin.store.points.none")
            example = texts.text("web.admin.store.rules.example", item=name, points=sold or none)
        return await sessions.render(
            request,
            "admin.StoreRuleApply",
            admin=admin,
            of=game,
            key=key,
            outcome=outcome,
            example=example,
            fields=change.fields,
        )

    @router.post("/admin/store/{game_id}/rules/{key}/apply")
    async def apply(request: Request, game_id: str, key: str) -> Response:
        admin, game, rules, change = await changed(request, game_id, key)
        if change.refused:
            raise HTTPException(400)
        assert game.store is not None
        _, _, items = await found(game, request.state.language)
        new = rules.model_copy(update={"prices": rules.prices | {key: change.rule}})
        outcome = applied(rules, new, key, items)
        seed = game.store.prices[key]
        prefix = f"{game.id}.store.prices.{key}."
        settings = {
            f"{prefix}{field}": None if value == written else value
            for field, value, written in _written(change.rule, seed)
        }
        details: dict[str, Any] = {
            "game": game.id,
            "rule": key,
            "before": _values(rules.prices[key]),
            "after": _values(change.rule),
            "changed": outcome.changed,
            "kept": outcome.kept,
        }
        await views.apply_rule(game, settings, outcome.points, details, admin)
        name = StoreTexts(game, request.state.language, datetime.now(UTC)).text(
            f"games.{game.id}.price_rule.{key}"
        )
        toast(request, "web.admin.toast.store.rule", rule=name, count=str(outcome.changed))
        return RedirectResponse(store_url(request, game, f"/rules?rule={key}"), 303)

    return router


def _change(texts: StoreTexts, rules: StoreRules, rule: PriceRule, form: FormData) -> RuleChange:
    # The rule with the form's value and ways of selling, or why they are not taken.
    fields = {"value": str(form.get("value", "")).strip()}
    price = whole_number(fields["value"], MOST_BALANCE)
    if price is None:
        refused = texts.text(
            "web.admin.store.refused.price",
            price=fields["value"] or "0",
            most=texts.amount(MOST_BALANCE),
        )
        return RuleChange(rule, fields, refused)
    if rule.use_price:
        fields["packs"] = str(form.get("packs", "")).strip()
        packs = [part.strip() for part in fields["packs"].split(",") if part.strip()]
        if not all(pack.isascii() and pack.isdecimal() and 0 < len(pack) < 7 for pack in packs):
            refused = texts.text("web.admin.store.refused.uses", uses=fields["packs"])
            return RuleChange(rule, fields, refused)
        sold = tuple(Ownership(COUNTED, uses=int(pack)) for pack in dict.fromkeys(packs))
        update: dict[str, Any] = {"use_price": price, "sold": sold}
    else:
        known = [f"{days}d" for days in sorted(rules.rentals)] + ["perm"]
        written = [value for value in known if form.get(f"sold-{value}")]
        fields |= {f"sold-{value}": "1" for value in written}
        sold = tuple(
            Ownership(PERMANENT) if value == "perm" else Ownership(RENTED, days=int(value[:-1]))
            for value in written
        )
        update = {"value": price, "sold": sold}
    changed = rule.model_copy(update=update)
    for ownership in changed.sold:
        # A rental's share or a pack's uses may make a price that no balance pays.
        cost = rules.price(changed, ownership) or 0
        if not 0 < cost <= MOST_BALANCE:
            refused = texts.text(
                "web.admin.store.refused.sold",
                point=texts.ownership(ownership, inline=True),
                price=texts.amount(cost),
                most=texts.amount(MOST_BALANCE),
            )
            return RuleChange(rule, fields, refused)
    return RuleChange(changed, fields)


def _written(rule: PriceRule, seed: PriceRule) -> list[tuple[str, str, str]]:
    # Each changeable field of a rule as the settings write it, beside the game data's.
    field = "use_price" if rule.use_price else "value"
    return [
        (field, str(getattr(rule, field)), str(getattr(seed, field))),
        ("sold", _sold(rule), _sold(seed)),
    ]


def _sold(rule: PriceRule) -> str:
    # In the order of an item's page, so that the game data's ways in another order are the same.
    return json.dumps([ownership.written for ownership in sorted(rule.sold, key=_order)])


def _values(rule: PriceRule) -> dict[str, Any]:
    # A rule's changeable values as the admin's log keeps them.
    return {
        "value": rule.value,
        "use_price": rule.use_price,
        "sold": [ownership.written for ownership in rule.sold],
    }


def _priced(texts: StoreTexts, currency: str, ownership: Ownership, price: int) -> str:
    # Such as "Funds, permanent: 2,600".
    return texts.text(
        "web.admin.store.rules.point",
        currency=texts.currency(currency),
        ownership=texts.ownership(ownership, inline=True),
        price=texts.amount(price),
    )


def _order(ownership: Ownership) -> tuple[bool, int]:
    # Rentals and uses from the shortest, then for good, as an item's page lists them.
    return ownership.type == PERMANENT, ownership.days or ownership.uses or 0
