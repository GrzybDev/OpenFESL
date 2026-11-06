import re
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.heroes import HeroRefused, NameCheck
from openfesl.core.personas import PersonaState
from openfesl.core.texts import localize
from openfesl.web.admin.accounts import AccountViews
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.heroes import HeroAdminViews, HeroPage
from openfesl.web.sessions import checked_form, toast

SECTION = "accounts"
# What moderators may change of a hero; the other changes need the admin role.
MODERATED = ("rename", "disable", "enable")
ACTIONS = (*MODERATED, "default", "portrait")


def hero_action_routes(
    sessions: AdminSessions,
    accounts: AccountViews,
    views: HeroAdminViews,
    apply: Callable[[], Awaitable[None]],
) -> APIRouter:
    """Return the changes to a hero that ask for a note.

    They are a new name, disabling and enabling it, making it its account's default hero and
    removing its portrait. Each one is a page of its own, which opens as a dialog over the hero's
    page where scripts run; a new name is checked as it is typed.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        accounts: Puts together what the panel shows about the accounts.
        views: Puts together what the panel shows about the heroes, and changes them.
        apply: Applies the recorded actions to the running server at once, which keeps a
            disabled hero out of its game.
    """
    router = APIRouter()
    path = "/admin/accounts/{account_id}/heroes/{hero_id}"

    def allowed(request: Request, action: str) -> Admin:
        if action not in ACTIONS:
            raise HTTPException(404)
        return sessions.admin(request, None if action in MODERATED else Role.ADMIN, SECTION)

    async def name_check(page: HeroPage, name: str) -> str:
        # A change of capitals alone keeps the hero's own name, which no other persona holds.
        rules, own = page.game.heroes, page.card.hero.persona.name
        assert rules is not None
        if name == own:
            return "same"
        if name.casefold() == own.casefold() and re.fullmatch(rules.name, name):
            return NameCheck.FREE
        return await views.heroes.check(page.game, name)

    async def form_page(
        request: Request, admin: Admin, page: HeroPage, action: str, back: str, **form: str
    ) -> HTMLResponse:
        return await sessions.render(
            request,
            "admin.HeroAction",
            400 if form else 200,
            admin=admin,
            page=page,
            action=action,
            back=back,
            form=form,
        )

    @router.get(path + "/rename/check", response_class=HTMLResponse)
    async def check(request: Request, account_id: str, hero_id: int, name: str = "") -> Response:
        sessions.admin(request)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        wanted = name.strip()
        found = await name_check(page, wanted) if wanted else ""
        return await sessions.render(request, "heroes.NameCheck", name=wanted, check=found)

    @router.get(path + "/{action}", response_class=HTMLResponse)
    async def ask(
        request: Request, account_id: str, hero_id: int, action: str, back: str = ""
    ) -> Response:
        admin = allowed(request, action)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        default = path.format(account_id=account_id, hero_id=hero_id)
        return await form_page(request, admin, page, action, back_to(request, back, default))

    @router.post(path + "/{action}")
    async def act(request: Request, account_id: str, hero_id: int, action: str) -> Response:
        admin = allowed(request, action)
        form = await checked_form(request)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        own = localize(path.format(account_id=account_id, hero_id=hero_id), request.state.language)
        back = back_to(request, str(form.get("back", "")), own)
        note = str(form.get("note", "")).strip()
        hero, game = page.card.hero, page.game
        persona, name = hero.persona, hero.persona.name
        if action == "rename":
            wanted = str(form.get("name", "")).strip()
            found = await name_check(page, wanted)
            if found != NameCheck.FREE or not note:
                error = "note" if found == NameCheck.FREE else "name"
                return await form_page(
                    request,
                    admin,
                    page,
                    action,
                    back,
                    name=wanted,
                    check=found,
                    note=note,
                    error=error,
                )
            try:
                await views.store.rename(persona, wanted, note, admin)
            except HeroRefused:
                toast(
                    request, "web.admin.toast.hero.not_renamed", error=True, hero=name, name=wanted
                )
                return RedirectResponse(back, 303)
            toast(request, "web.admin.toast.hero.renamed", old=name, new=wanted, game=game.name)
            return RedirectResponse(back, 303)
        if not note:
            return await form_page(request, admin, page, action, back, error="note")
        if action in ("disable", "enable"):
            disabling = action == "disable"
            if hero.disabled == disabling:
                toast(request, f"web.admin.toast.hero.{action}d_already", error=True, hero=name)
                return RedirectResponse(back, 303)
            moved = page.next_default if disabling and page.card.default else None
            state = PersonaState.DISABLED if disabling else PersonaState.ACTIVE
            await views.store.set_state(persona, game.id, state, moved, note, admin)
            await apply()
            default = moved or page.roster.default
            if moved or (not disabling and default and default != hero):
                key = f"web.admin.toast.hero.{action}d.default"
            else:
                key = f"web.admin.toast.hero.{action}d"
            toast(request, key, hero=name, default=default.persona.name if default else "")
        elif action == "default":
            if not hero.playable:
                toast(request, "web.admin.toast.hero.default_refused", error=True, hero=name)
            else:
                await views.store.make_default(persona, game.id, page.roster.default, note, admin)
                toast(request, "web.admin.toast.hero.default", hero=name, game=game.name)
        elif await views.store.remove_mugshot(persona, note, admin):
            toast(request, "web.admin.toast.hero.portrait", hero=name)
        else:
            toast(request, "web.admin.toast.hero.portrait_none", error=True, hero=name)
        return RedirectResponse(back, 303)

    return router
