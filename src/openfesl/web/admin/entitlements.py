from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.entitlements import Entitlement, EntitlementQuery, StatusChange, StoredKey
from openfesl.core.games import Games, Grant
from openfesl.core.texts import localize
from openfesl.web.admin.accounts import AccountView, AccountViews
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.keys import KeyView, key_views

SECTION = "accounts"
# The grant form's choice of one tag typed by hand instead of a product key's.
ONE_TAG = "tag"


class EntitlementAdmin(Protocol):
    """Where the admin panel reads and changes the accounts' entitlements."""

    async def of_account(self, account_id: int, query: EntitlementQuery) -> list[Entitlement]:
        """Return an account's entitlements, filtered by whichever filters are not empty.

        Args:
            account_id: The account.
            query: The filters.
        """

    async def changes(self, account_id: int) -> dict[str, StatusChange]:
        """Return when the admin last revoked or granted again each of an account's entitlements.

        Args:
            account_id: The account.
        """

    async def keys(self) -> list[StoredKey]:
        """Return every product key with what it grants, in the order they were added."""

    async def give(
        self, account_id: int, grants: Sequence[Grant], key: str | None, admin: Admin
    ) -> list[str]:
        """Grant an account entitlements by hand; those it holds stay as they are.

        Args:
            account_id: The account.
            grants: The entitlements.
            key: The product key whose grants they are, if any.
            admin: The admin who granted them.

        Returns:
            The tags granted.
        """

    async def set_status(self, account_id: int, tag: str, active: bool, admin: Admin) -> bool:
        """Revoke an account's entitlement or grant it again.

        Args:
            account_id: The account.
            tag: The entitlement's tag.
            active: Whether it is granted again; otherwise it is revoked.
            admin: The admin who changed it.

        Returns:
            Whether it changed.
        """


@dataclass(frozen=True, slots=True)
class KeyChoice:
    """A product key as the grant form offers it for an account.

    Attributes:
        view: The key.
        had: How many of its tags the account holds already.
    """

    view: KeyView
    had: int

    @property
    def open(self) -> bool:
        """Whether it grants the account anything new."""
        return self.had < len(self.view.key.grants)


def entitlement_routes(
    sessions: AdminSessions, views: AccountViews, store: EntitlementAdmin, games: Games
) -> APIRouter:
    """Return granting entitlements to an account, revoking them and granting them again.

    Each needs the admin role. Granting picks an enabled product key, whose grants the account
    gets without using the key up, or one tag typed by hand; revoking asks first.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Puts together what the panel shows about the accounts.
        store: Where the entitlements and product keys are kept.
        games: The games, whose data names their starting keys.
    """
    router = APIRouter()

    async def account(account_id: str) -> AccountView:
        view = await views.find(account_id)
        if view is None:
            raise HTTPException(404)
        return view

    def page_of(request: Request, view: AccountView) -> str:
        return localize(f"/admin/accounts/{view.record.account.id}", request.state.language)

    async def grant_page(
        request: Request, admin: Admin, view: AccountView, status: int = 200, **form: str
    ) -> HTMLResponse:
        found = await store.of_account(view.record.account.id, EntitlementQuery())
        held = {entitlement.tag for entitlement in found}
        keys = [
            KeyChoice(entry, sum(grant.tag in held for grant in entry.key.grants))
            for entry in key_views(games, await store.keys())
            if entry.key.enabled
        ]
        return await sessions.render(
            request,
            "admin.AccountGrant",
            status,
            admin=admin,
            view=view,
            keys=keys,
            held=held,
            back=page_of(request, view),
            form=form,
        )

    @router.get("/admin/accounts/{account_id}/grant", response_class=HTMLResponse)
    async def choose(request: Request, account_id: str) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        return await grant_page(request, admin, await account(account_id))

    @router.post("/admin/accounts/{account_id}/grant")
    async def grant(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        view = await account(account_id)
        chosen = str(form.get("key", ""))
        fields = {name: str(form.get(name, "")).strip() for name in ("tag", "group", "product")}
        if chosen == ONE_TAG:
            if not fields["tag"]:
                return await grant_page(request, admin, view, 400, key=chosen, **fields)
            group, product = fields["group"] or None, fields["product"] or None
            grants, code = [Grant(tag=fields["tag"], group=group, product=product)], None
        else:
            keys = await store.keys()
            found = next((key for key in keys if key.enabled and key.code == chosen), None)
            if found is None:
                return await grant_page(request, admin, view, 400, key=chosen, error="key")
            grants, code = list(found.grants), found.code
        added = await store.give(view.record.account.id, grants, code, admin)
        name = view.record.account.nuid
        if added:
            sessions.toast(request, "web.admin.toast.granted", name=name, tags=", ".join(added))
        else:
            sessions.toast(request, "web.admin.toast.granted_none", error=True, name=name)
        return RedirectResponse(page_of(request, view), 303)

    @router.get("/admin/accounts/{account_id}/entitlements/revoke", response_class=HTMLResponse)
    async def confirm_revoke(
        request: Request, account_id: str, tag: str = "", back: str = ""
    ) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        view = await account(account_id)
        held = await store.of_account(view.record.account.id, EntitlementQuery(tag=tag))
        if not tag or not held:
            raise HTTPException(404)
        return await sessions.render(
            request,
            "admin.AccountRevoke",
            admin=admin,
            view=view,
            entitlement=held[0],
            back=back_to(request, back, f"/admin/accounts/{view.record.account.id}"),
        )

    for decision in ("revoke", "restore"):
        router.add_api_route(
            f"/admin/accounts/{{account_id}}/entitlements/{decision}",
            _decide(sessions, account, store, decision),
            methods=["POST"],
        )
    return router


def _decide(
    sessions: AdminSessions,
    account: Callable[[str], Awaitable[AccountView]],
    store: EntitlementAdmin,
    decision: str,
) -> Callable[[Request, str], Awaitable[Response]]:
    async def decide(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        view = await account(account_id)
        tag = str(form.get("tag", ""))
        path = f"/admin/accounts/{view.record.account.id}"
        back = back_to(request, str(form.get("back", "")), path)
        if decision == "revoke" and form.get("confirmed") != "1":
            target = localize(f"{path}/entitlements/revoke", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'tag': tag, 'back': back})}", 303)
        if await store.set_status(view.record.account.id, tag, decision == "restore", admin):
            sessions.toast(request, f"web.admin.toast.{decision}d", tag=tag)
        else:
            sessions.toast(request, "web.admin.toast.entitlement_unchanged", error=True, tag=tag)
        return RedirectResponse(back, 303)

    return decide
