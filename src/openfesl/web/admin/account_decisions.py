from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.accounts import AccountState
from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.personas import PersonaState
from openfesl.core.texts import localize
from openfesl.web.admin.accounts import AccountView, AccountViews, PersonaView
from openfesl.web.admin.auth import AdminSessions, back_to

# The decisions about an account or one of its names that ask for confirmation first.
CONFIRMED = ("end", "kick", "disable")


class AccountChanges(Protocol):
    """Changes what an account may do, each change recorded among the admin's actions."""

    async def set_state(
        self, account_id: int, state: AccountState, until: datetime | None, admin: Admin | None
    ) -> None:
        """Change whether an account may log in.

        Args:
            account_id: The account.
            state: Its new state.
            until: When the state ends, if it does.
            admin: The admin who changed it.
        """

    async def confirm_account(self, account_id: int, admin: Admin | None) -> None:
        """Confirm an account's address by hand.

        Args:
            account_id: The account.
            admin: The admin who confirmed it.
        """

    async def forget(self, account_id: int, login_ids: list[str] | None, admin: Admin) -> int:
        """Remove logins that the game remembers for an account.

        Args:
            account_id: The account.
            login_ids: The logins by their ids; None removes every one.
            admin: The admin who removed them.

        Returns:
            How many were removed.
        """


class PersonaChanges(Protocol):
    """Changes whether personas may play."""

    async def set_state(self, persona_id: int, state: PersonaState, admin: Admin | None) -> None:
        """Change whether a persona may play and record it among the admin's actions.

        Args:
            persona_id: The persona's number.
            state: Its new state.
            admin: The admin who changed it.
        """


class ActionLog(Protocol):
    """Records the admin's actions that change nothing stored, for the server to apply."""

    async def record(self, action: AdminAction, target: str, admin: Admin | None) -> None:
        """Record an action, such as a kick.

        Args:
            action: What the admin did.
            target: What it was done to.
            admin: The admin who did it.
        """


def account_decision_routes(
    sessions: AdminSessions,
    views: AccountViews,
    accounts: AccountChanges,
    personas: PersonaChanges,
    log: ActionLog,
    apply: Callable[[], Awaitable[None]],
) -> APIRouter:
    """Return the decisions about a player's account, its session, logins and names.

    Ending a session, kicking a name from its game and disabling a name ask first, which
    without scripts is a page of their own; changing the state is a form of its own.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Puts together what the panel shows about the accounts.
        accounts: Changes the accounts.
        personas: Changes the personas.
        log: Records the actions the running server applies.
        apply: Applies the recorded actions to the running server at once.
    """
    router = APIRouter()

    async def account(request: Request, account_id: str) -> AccountView:
        view = await views.find(account_id)
        if view is None:
            raise HTTPException(404)
        return view

    def persona_of(view: AccountView, persona_id: str) -> PersonaView:
        found = next(
            (entry for entry in view.personas if str(entry.record.persona.id) == persona_id), None
        )
        if found is None:
            raise HTTPException(404)
        return found

    def page_of(request: Request, view: AccountView) -> str:
        return localize(_path(view), request.state.language)

    async def state_page(
        request: Request, view: AccountView, status: int = 200, **form: str
    ) -> HTMLResponse:
        return await sessions.render(
            request,
            "admin.AccountState",
            status,
            admin=sessions.admin(request),
            view=view,
            states=list(AccountState),
            back=page_of(request, view),
            form=form,
        )

    @router.get("/admin/accounts/{account_id}/state", response_class=HTMLResponse)
    async def change_state(request: Request, account_id: str) -> HTMLResponse:
        sessions.admin(request)
        return await state_page(request, await account(request, account_id))

    @router.post("/admin/accounts/{account_id}/state")
    async def set_state(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await account(request, account_id)
        chosen = str(form.get("state", ""))
        written = str(form.get("until", "")).strip()
        if chosen not in set(AccountState):
            return await state_page(request, view, 400, until=written, error="state")
        state = AccountState(chosen)
        until = None
        if written and state is not AccountState.ACTIVE:
            try:
                until = datetime.fromisoformat(written).replace(tzinfo=UTC)
            except ValueError:
                until = None
            if until is None or until <= datetime.now(UTC):
                return await state_page(
                    request, view, 400, state=chosen, until=written, error="until"
                )
        await accounts.set_state(view.record.account.id, state, until, admin)
        await apply()
        name = view.record.account.nuid
        sessions.toast(request, f"web.admin.toast.account.{state}", name=name)
        return RedirectResponse(page_of(request, view), 303)

    @router.post("/admin/accounts/{account_id}/verify")
    async def verify(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await account(request, account_id)
        name = view.record.account.nuid
        if view.record.account.verification_expires_at is None:
            sessions.toast(request, "web.admin.toast.verified_already", error=True, name=name)
        else:
            await accounts.confirm_account(view.record.account.id, admin)
            sessions.toast(request, "web.admin.toast.verified", name=name)
        return RedirectResponse(page_of(request, view), 303)

    @router.post("/admin/accounts/{account_id}/remembered/remove")
    async def forget(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await account(request, account_id)
        login = form.get("login")
        chosen = [str(login)] if login else None
        removed = await accounts.forget(view.record.account.id, chosen, admin)
        name = view.record.account.nuid
        if removed:
            sessions.toast(request, "web.admin.toast.forgotten", name=name, count=str(removed))
        else:
            sessions.toast(request, "web.admin.toast.forgotten_none", error=True, name=name)
        return RedirectResponse(page_of(request, view), 303)

    @router.post("/admin/accounts/{account_id}/names/{persona_id}/enable")
    async def enable(request: Request, account_id: str, persona_id: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await account(request, account_id)
        persona = persona_of(view, persona_id)
        await personas.set_state(persona.record.persona.id, PersonaState.ACTIVE, admin)
        sessions.toast(request, "web.admin.toast.enabled", name=persona.record.persona.name)
        return RedirectResponse(page_of(request, view), 303)

    for decision in CONFIRMED:
        path = "/session/end" if decision == "end" else f"/names/{{persona_id}}/{decision}"
        router.add_api_route(
            f"/admin/accounts/{{account_id}}{path}",
            _confirmation(sessions, account, persona_of, decision),
            response_class=HTMLResponse,
        )

    @router.post("/admin/accounts/{account_id}/session/end")
    async def end(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await account(request, account_id)
        back = back_to(request, str(form.get("back", "")), _path(view))
        name = view.record.account.nuid
        if form.get("confirmed") != "1":
            return _ask(request, f"/admin/accounts/{account_id}/session/end", back)
        if view.session is None:
            sessions.toast(request, "web.admin.toast.ended_already", error=True, name=name)
        else:
            await log.record(AdminAction.SESSION_END, str(view.record.account.id), admin)
            await apply()
            sessions.toast(request, "web.admin.toast.ended", name=name)
        return RedirectResponse(back, 303)

    @router.post("/admin/accounts/{account_id}/names/{persona_id}/{decision}")
    async def decide(request: Request, account_id: str, persona_id: str, decision: str) -> Response:
        if decision not in ("kick", "disable"):
            raise HTTPException(404)
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await account(request, account_id)
        persona = persona_of(view, persona_id)
        back = back_to(request, str(form.get("back", "")), _path(view))
        if form.get("confirmed") != "1":
            path = f"/admin/accounts/{account_id}/names/{persona_id}/{decision}"
            return _ask(request, path, back)
        name = persona.record.persona.name
        target = persona.record.persona.id
        if decision == "kick":
            await log.record(AdminAction.PLAYER_KICK, str(target), admin)
        else:
            await personas.set_state(target, PersonaState.DISABLED, admin)
        await apply()
        sessions.toast(request, f"web.admin.toast.{decision}", name=name)
        return RedirectResponse(back, 303)

    return router


def _path(view: AccountView) -> str:
    return f"/admin/accounts/{view.record.account.id}"


def _ask(request: Request, path: str, back: str) -> Response:
    target = localize(path, request.state.language)
    return RedirectResponse(f"{target}?{urlencode({'back': back})}", 303)


def _confirmation(
    sessions: AdminSessions,
    account: Callable[[Request, str], Awaitable[AccountView]],
    persona_of: Callable[[AccountView, str], PersonaView],
    decision: str,
) -> Callable[..., Awaitable[HTMLResponse]]:
    async def confirm(
        request: Request, account_id: str, persona_id: str = "", back: str = ""
    ) -> HTMLResponse:
        admin = sessions.admin(request)
        view = await account(request, account_id)
        persona = persona_of(view, persona_id) if decision != "end" else None
        return await sessions.render(
            request,
            "admin.AccountConfirm",
            admin=admin,
            view=view,
            persona=persona,
            decision=decision,
            back=back_to(request, back, _path(view)),
        )

    return confirm
