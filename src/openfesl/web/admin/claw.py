from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Role
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.boxes import BoxAdminViews, boxes_page
from openfesl.web.admin.claw_panel import FIELDS, claw_form, read_form
from openfesl.web.admin.store import SECTION, store_url
from openfesl.web.sessions import checked_form, toast
from openfesl.web.store_texts import StoreTexts


def claw_routes(sessions: AdminSessions, views: BoxAdminViews) -> APIRouter:
    """Return the form that changes each game's Claw, which needs the admin role.

    A refusal shows the boxes page again with why; a save applies at once to the site and is
    recorded with its note, each changed value before and after.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the boxes pages show, with the Claw and its settings.
    """
    router = APIRouter()

    @router.post("/admin/store/{game_id}/claw")
    async def save_claw(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = views.stores.game(game_id)
        rules = await views.claw.rules(game)
        if rules is None or game.claw is None:
            raise HTTPException(404)
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        currencies = [wallet.currency for wallet in game.site.wallets] if game.site else []
        sent = read_form(form, currencies, texts)
        box = await views.claw.box(game)
        rare = box is not None and any(drop.rare for drop in box.drops)
        if sent.values["feed"] and not rare:
            sent.refused["feed"] = texts.text("web.admin.boxes.claw.refused.no_rare")
        if sent.refused:
            return await boxes_page(sessions, views, request, admin, game, sent)
        before, after = claw_form(rules).values, sent.values
        changes = [[name, before[name], after[name]] for name in FIELDS.values()]
        changes = [change for change in changes if change[1] != change[2]]
        if changes:
            data = claw_form(game.claw).values
            settings = {
                key: None if after[name] == data[name] else _setting(after[name], name)
                for key, name in FIELDS.items()
            }
            details: dict[str, Any] = {"changes": changes, "note": after["note"]}
            await views.change_claw(game, settings, details, admin)
        _said(request, texts, changes, after)
        return RedirectResponse(store_url(request, game, "/boxes#boxes-claw"), 303)

    return router


def _setting(value: str, name: str) -> str:
    # A setting's value as the Claw's rules read it.
    if name in ("runs", "paid", "feed"):
        return "on" if value else "off"
    return value


def _said(
    request: Request, texts: StoreTexts, changes: list[list[str]], after: dict[str, str]
) -> None:
    # The toast of a save: the Claw switched, a new price, or its settings saved.
    changed = {name for name, _, _ in changes}
    if "runs" in changed:
        toast(request, f"web.admin.toast.claw.{'on' if after['runs'] else 'off'}")
    elif changed & {"price", "currency"}:
        price = texts.priced(int(after["price"]), after["currency"])
        toast(request, "web.admin.toast.claw.price", price=price)
    elif changed:
        toast(request, "web.admin.toast.claw.saved")
    else:
        toast(request, "web.admin.toast.claw.unchanged")
