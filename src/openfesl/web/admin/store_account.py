from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.admins import Admin, Role
from openfesl.core.games import Game
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import SECTION, StoreAdminViews, store_url
from openfesl.web.admin.wallets import shared_currencies
from openfesl.web.pages import whole_number
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

# The unit of the chests a hero brings from a round, which is no currency.
CHESTS = "chests"


@dataclass(frozen=True, slots=True)
class AccountValue:
    """A price, allowance or per-round cap of a game's accounts, as its store's prices list it.

    Attributes:
        name: Its name in the form, such as slot or allowance-_PF: what it is, then the code of
            its currency where there is one of each.
        path: The path of the setting that overrides it, after the game's id.
        unit: The currency it is counted in, or chests.
        data: Its value in the game's data.
        value: Its value as the instance sets it.
        least: The least value it may take.
    """

    name: str
    path: str
    unit: str
    data: int
    value: int
    least: int


def account_values(data: Game, live: Game) -> list[AccountValue]:
    """Return the account prices, allowances and per-round caps of a game.

    Args:
        data: The game as its data gives it.
        live: The game with the instance's settings over its data.
    """
    named: list[tuple[str, str, str, int]] = []
    if data.heroes:
        currency = data.heroes.currency
        named += [("slot", "heroes.slot_price", currency, 1)]
        named += [("rename", "heroes.rename_price", currency, 1)]
    if data.chests:
        named += [("key", "chests.key_price", data.chests.currency, 1)]
    shared = shared_currencies(data)
    named += [(f"allowance-{code}", f"currencies.{code}.allowance", code, 0) for code in shared]
    named += [
        (f"credits-{code}", f"currencies.{code}.credits.most", code, 0)
        for code in shared
        if data.currencies[code].credits
    ]
    if data.chests:
        named += [("chests", "chests.most", CHESTS, 1)]
    return [
        AccountValue(name, path, unit, _at(data, path), _at(live, path), least)
        for name, path, unit, least in named
    ]


def store_account_routes(sessions: AdminSessions, views: StoreAdminViews) -> APIRouter:
    """Return the account prices, allowances and per-round caps of each game with a store.

    Every role reads them; changing them needs the admin role. They are saved together, nothing
    while one of them is refused, each change recorded among the admin's actions, and apply at
    once: a price to the next purchase, an allowance to wallets opened from then on and a cap to
    the next round a server reports.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Name the games with stores and save the values.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/account"

    async def page(
        request: Request,
        admin: Admin,
        game: Game,
        form: Mapping[str, str],
        refused: Mapping[str, str] | None = None,
    ) -> HTMLResponse:
        values = account_values(game, await views.account_rules(game))
        # The values a form holds, as a script sends them while they are typed or a refused
        # form sent them, else those set; each is changed while it is not the game data's.
        typed = {value.name: form.get(value.name, str(value.value)) for value in values}
        changed = [
            value.name
            for value in values
            if whole_number(typed[value.name], MOST_BALANCE, 0) != value.data
        ]
        return await sessions.render(
            request,
            "admin.StoreAccount",
            400 if refused else 200,
            admin=admin,
            of=game,
            values=values,
            typed=typed,
            changed=changed,
            refused=dict(refused or {}),
            manages=admin.role is Role.ADMIN,
        )

    @router.get(path, response_class=HTMLResponse)
    async def account(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        return await page(request, admin, game, request.query_params)

    @router.post(path)
    async def save(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.game(game_id)
        form = await request.form()
        check_form(request, form)
        fields = {key: str(value) for key, value in form.items()}
        values = account_values(game, await views.account_rules(game))
        # Reset puts a value back to the game data's in the form, saving nothing.
        if reset := next((value for value in values if value.name == fields.get("reset")), None):
            return await page(request, admin, game, fields | {reset.name: str(reset.data)})
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        refused, settings, changes = {}, {}, []
        key = None
        for value in values:
            text = fields.get(value.name, "").strip()
            number = whole_number(text, MOST_BALANCE, value.least)
            if number is None:
                refused[value.name] = texts.text(
                    "web.admin.store.account.refused",
                    value=text or "0",
                    least=texts.amount(value.least),
                    most=texts.amount(MOST_BALANCE),
                )
            elif number != value.value:
                setting = f"{game.id}.{value.path}"
                settings[setting] = None if number == value.data else str(number)
                changes.append(_change(game, value, number))
                if value.name == "key" and game.chests:
                    key = (game.chests.key, number)
        if refused:
            return await page(request, admin, game, fields, refused)
        if changes:
            await views.save_account(game, settings, key, changes, admin)
            toast(request, "web.admin.toast.store.account", count=str(len(changes)))
        else:
            toast(request, "web.admin.toast.store.account.unchanged")
        return RedirectResponse(store_url(request, game, "/account"), 303)

    return router


def _change(game: Game, value: AccountValue, number: int) -> dict[str, Any]:
    # A value's change as the admin's log keeps it.
    return {
        "game": game.id,
        "name": value.name,
        "unit": value.unit,
        "before": value.value,
        "after": number,
    }


def _at(game: Game, path: str) -> int:
    # The value that a setting's path names in a game.
    found: Any = game
    for part in path.split("."):
        found = found[part] if isinstance(found, dict) else getattr(found, part)
    return int(found)
