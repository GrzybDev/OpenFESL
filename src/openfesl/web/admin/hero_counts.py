from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.chests import key_tag
from openfesl.web.admin.accounts import AccountView, AccountViews
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.heroes import GameHeroes, HeroAdminViews
from openfesl.web.admin.wallets import counted_change
from openfesl.web.pages import number
from openfesl.web.sessions import checked_form, toast, toast_at

SECTION = "accounts"


@dataclass(frozen=True, slots=True)
class Counted:
    """What a count form changes: an account's hero slots or keys, or a hero's chests.

    Attributes:
        kind: slots, keys or chests.
        view: The account.
        found: The account's heroes in the game.
        hero: The hero whose chests are counted; none for the account's slots and keys.
        now: How many there are now.
    """

    kind: str
    view: AccountView
    found: GameHeroes
    hero: int | None
    now: int


def hero_count_routes(
    sessions: AdminSessions, accounts: AccountViews, views: HeroAdminViews
) -> APIRouter:
    """Return granting an account hero slots, and granting or taking its keys or a hero's chests.

    Each needs the admin role and asks for a note; a take of more than is held is refused. Each
    is a page of its own, which opens as a dialog over its page where scripts run.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        accounts: Puts together what the panel shows about the accounts.
        views: Puts together what the panel shows about the heroes, and changes them.
    """
    router = APIRouter()

    async def counted(request: Request, kind: str, account_id: str, key: str) -> Counted:
        view = await accounts.found(account_id)
        for heroes in await views.of_account(view.record.account.id, request.state.language):
            if kind == "chests":
                card = next((c for c in heroes.cards if str(c.hero.persona.id) == key), None)
                if card is not None and heroes.game.chests:
                    return Counted(kind, view, heroes, card.hero.persona.id, card.chests or 0)
            elif heroes.game.id == key and (kind == "slots" or heroes.keys is not None):
                now = heroes.roster.slots if kind == "slots" else heroes.keys or 0
                return Counted(kind, view, heroes, None, now)
        raise HTTPException(404)

    def back_of(request: Request, shown: Counted, sent: str) -> str:
        path = f"/admin/accounts/{shown.view.record.account.id}"
        return back_to(request, sent, f"{path}/heroes/{shown.hero}" if shown.hero else path)

    async def ask(request: Request, kind: str, account_id: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        shown = await counted(request, kind, account_id, key)
        back = back_of(request, shown, request.query_params.get("back", ""))
        return await sessions.render(
            request, "admin.CountForm", admin=admin, shown=shown, back=back, form={}
        )

    async def act(request: Request, kind: str, account_id: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        shown = await counted(request, kind, account_id, key)
        back = back_of(request, shown, str(form.get("back", "")))
        amount, taking, note, refused = counted_change(form, shown.now, takes=kind != "slots")
        if refused:
            return await sessions.render(
                request, "admin.CountForm", 400, admin=admin, shown=shown, back=back, form=refused
            )
        return await _apply(request, views, shown, amount, taking, note, admin, back)

    @router.get("/admin/accounts/{account_id}/games/{game}/{kind}", response_class=HTMLResponse)
    async def ask_account(request: Request, account_id: str, game: str, kind: str) -> Response:
        if kind not in ("slots", "keys"):
            raise HTTPException(404)
        return await ask(request, kind, account_id, game)

    @router.post("/admin/accounts/{account_id}/games/{game}/{kind}")
    async def change_account(request: Request, account_id: str, game: str, kind: str) -> Response:
        if kind not in ("slots", "keys"):
            raise HTTPException(404)
        return await act(request, kind, account_id, game)

    @router.get("/admin/accounts/{account_id}/heroes/{hero_id}/chests", response_class=HTMLResponse)
    async def ask_chests(request: Request, account_id: str, hero_id: str) -> Response:
        return await ask(request, "chests", account_id, hero_id)

    @router.post("/admin/accounts/{account_id}/heroes/{hero_id}/chests")
    async def change_chests(request: Request, account_id: str, hero_id: str) -> Response:
        return await act(request, "chests", account_id, hero_id)

    return router


async def _apply(
    request: Request,
    views: HeroAdminViews,
    shown: Counted,
    amount: int,
    taking: bool,
    note: str,
    admin: Admin,
    address: str,
) -> Response:
    game = shown.found.game
    account = shown.view.record.account
    language = request.state.language
    if shown.kind == "slots":
        await views.store.grant_slots(account.id, game.id, amount, note, admin)
        return toast_at(
            request,
            address,
            "web.admin.toast.slots",
            count=number(amount, language),
            account=account.nuid,
        )
    assert game.chests is not None
    hero = next(
        (card.hero for card in shown.found.cards if card.hero.persona.id == shown.hero), None
    )
    owner = hero.persona if hero else account.id
    tag = game.chests.tag if hero else key_tag(game.chests)
    before, done = await views.store.count(
        owner, game.id, tag, -amount if taking else amount, note, admin
    )
    holder = hero.persona.name if hero else account.nuid
    count, held = number(amount, language), number(before, language)
    after = number(before - amount if taking else before + amount, language)
    key = f"web.admin.toast.{shown.kind}.{'taken' if taking else 'granted'}"
    if not done:
        key = f"web.admin.toast.{shown.kind}.fewer"
    toast(request, key, error=not done, count=count, holder=holder, held=held, after=after)
    return RedirectResponse(address, 303)
