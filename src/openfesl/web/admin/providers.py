from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.games import Games
from openfesl.core.servers import (
    MAXIMUM_PROVIDER_NAME,
    PROVIDER_ID_SIZE,
    Provider,
    ProviderHistory,
    new_provider,
    provider_file,
)
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.servers import ServerView, ServerViews

# The tabs of the providers page with their sortable columns, the first the default order.
TABS = {
    "active": ("name", "approved", "waiting", "added"),
    "revoked": ("name", "approved", "waiting", "added"),
}
DESCENDING = {"approved", "waiting", "added"}
SECTION = "providers"


@dataclass(frozen=True, slots=True)
class ProviderView:
    """A provider as the admin panel lists it, with its servers.

    Attributes:
        provider: The provider.
        history: When it was added and revoked, and by whom.
        servers: The servers that report it, newest first.
    """

    provider: Provider
    history: ProviderHistory
    servers: list[ServerView] = field(default_factory=list)

    @property
    def tab(self) -> str:
        """The tab of the providers page that lists it: active or revoked."""
        return "revoked" if self.provider.revoked else "active"

    def count(self, tab: str) -> int:
        """Return how many of its servers the servers page lists in a tab.

        Args:
            tab: waiting, approved or revoked.
        """
        return sum(view.tab == tab for view in self.servers)

    @property
    def hosting(self) -> list[ServerView]:
        """Its approved servers that host a game now."""
        return [view for view in self.servers if view.tab == "approved" and view.hosting]

    @property
    def players(self) -> int:
        """How many players are in the games its servers host now."""
        return sum(view.hosting.players for view in self.servers if view.hosting)


@dataclass(frozen=True, slots=True)
class ProviderList:
    """Which providers the providers page lists and how, as its address says.

    Attributes:
        tab: The tab: active or revoked.
        sort: The column the list is sorted by.
        descending: Whether the list runs from the highest value down.
        open: The provider whose details are open, by its id in hex.
    """

    tab: str = "active"
    sort: str = "name"
    descending: bool = False
    open: str | None = None

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        tab = query.get("state", "")
        tab = tab if tab in TABS else "active"
        sort = query.get("sort", "")
        sort = sort if sort in TABS[tab] else TABS[tab][0]
        order = query.get("order", "")
        return cls(
            tab=tab,
            sort=sort,
            descending=order == "desc" if order in ("asc", "desc") else sort in DESCENDING,
            open=query.get("open") or None,
        )

    def changed(self, **changes: Any) -> ProviderList:
        """Return the list with some choices changed and no details open.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **({"open": None} | changes))

    def switched(self, tab: str) -> ProviderList:
        """Return the list in another tab, in that tab's own order.

        Args:
            tab: The tab.
        """
        return ProviderList(tab=tab, sort=TABS[tab][0], descending=TABS[tab][0] in DESCENDING)

    def sorted_by(self, column: str) -> ProviderList:
        """Return the list sorted by a column, the other way round when it already is.

        Args:
            column: The column.
        """
        descending = not self.descending if column == self.sort else column in DESCENDING
        return self.changed(sort=column, descending=descending)

    def link(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        fields = [
            ("state", "" if self.tab == "active" else self.tab),
            ("sort", "" if self.sort == TABS[self.tab][0] else self.sort),
            ("order", "" if self.descending == (self.sort in DESCENDING) else self.direction),
            ("open", self.open or ""),
        ]
        query = urlencode([(key, value) for key, value in fields if value])
        return f"?{query}" if query else ""

    @property
    def direction(self) -> str:
        """The sort order as the address names it."""
        return "desc" if self.descending else "asc"


def provider_routes(
    sessions: AdminSessions,
    games: Games,
    servers: ServerViews,
    apply: Callable[[], Awaitable[None]],
) -> APIRouter:
    """Return the providers page, adding a provider, its ProviderId.dat and its revocation.

    Moderators see the providers; adding one, its file and revoking one need the admin role.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, of which those whose servers report providers read the file.
        servers: Puts together what the panel shows about the dedicated servers.
        apply: Ends the games of servers the admin revoked.
    """
    router = APIRouter()
    readers = [game for game in games if game.provider_attribute]
    files = next((game.server_files for game in readers if game.server_files), None)
    # The file's name as the games' server releases read it.
    file_name = files.provider if files else "ProviderId.dat"

    async def views() -> tuple[list[ProviderView], list[ServerView]]:
        every = await servers.all()
        history = await servers.store.provider_history()
        found = [
            ProviderView(provider, history[provider.id])
            for provider in await servers.store.providers()
        ]
        by_id = {view.provider.id: view for view in found}
        for server in every:
            if server.server.provider_id in by_id:
                by_id[server.server.provider_id].servers.append(server)
        return found, every

    async def page(request: Request, admin: Admin, status: int = 200, **form: Any) -> HTMLResponse:
        listing = ProviderList.read(request.query_params)
        found, every = await views()
        added = request.query_params.get("added", "")
        shown = sorted(
            (view for view in found if view.tab == listing.tab),
            key=lambda view: _sort_key(listing.sort, view),
            reverse=listing.descending,
        )
        return await sessions.render(
            request,
            "admin.Providers",
            status,
            admin=admin,
            listing=listing,
            providers=shown,
            counts={tab: sum(view.tab == tab for view in found) for tab in TABS},
            unmatched=sum(
                view.server.provider_id is not None and not view.server.provider for view in every
            ),
            added=next((view for view in found if view.provider.id.hex() == added), None),
            readers=readers,
            files=files,
            file_name=file_name,
            file_size=len(provider_file(bytes(PROVIDER_ID_SIZE))),
            longest=MAXIMUM_PROVIDER_NAME,
            mode=(await servers.store.hosting_mode()).value,
            manages=admin.role is Role.ADMIN,
            form=form,
        )

    async def find(provider: str) -> ProviderView:
        found, _every = await views()
        view = next((view for view in found if view.provider.id.hex() == provider.lower()), None)
        if view is None:
            raise HTTPException(404)
        return view

    @router.get("/admin/providers", response_class=HTMLResponse)
    async def providers(request: Request) -> HTMLResponse:
        return await page(request, sessions.admin(request))

    @router.post("/admin/providers")
    async def add(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        name = str(form.get("name", "")).strip()
        try:
            provider = new_provider(name)
        except ValueError:
            return await page(request, admin, 400, name=name, error="length")
        if not await servers.store.add_provider(provider, admin):
            known = await servers.store.providers()
            taken = next(
                (found.name for found in known if found.name.casefold() == name.casefold()), name
            )
            return await page(request, admin, 400, name=name, error="taken", taken=taken)
        target = localize("/admin/providers", request.state.language)
        return RedirectResponse(f"{target}?{urlencode({'added': provider.id.hex()})}", 303)

    @router.get("/admin/providers/{provider}")
    async def provider(request: Request, provider: str) -> Response:
        sessions.admin(request)
        view = await find(provider)
        listing = ProviderList(tab=view.tab, open=view.provider.id.hex())
        target = localize("/admin/providers", request.state.language)
        return RedirectResponse(f"{target}{listing.link()}#row-{listing.open}", 303)

    @router.get("/admin/providers/{provider}/provider-id.dat")
    async def download(request: Request, provider: str) -> Response:
        sessions.admin(request, Role.ADMIN, SECTION)
        view = await find(provider)
        if view.provider.revoked:
            raise HTTPException(404)
        return Response(
            provider_file(view.provider.id),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
        )

    @router.get("/admin/providers/{provider}/revoke", response_class=HTMLResponse)
    async def confirm_revoke(request: Request, provider: str, back: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        view = await find(provider)
        return await sessions.render(
            request,
            "admin.ConfirmProvider",
            admin=admin,
            view=view,
            file_name=file_name,
            back=back_to(request, back, "/admin/providers"),
        )

    @router.post("/admin/providers/{provider}/revoke")
    async def revoke(request: Request, provider: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        view = await find(provider)
        back = back_to(request, str(form.get("back", "")), "/admin/providers")
        name = view.provider.name
        if view.provider.revoked:
            sessions.toast(request, "web.admin.toast.provider_unchanged", error=True, name=name)
        elif form.get("confirmed") != "1":
            target = localize(f"/admin/providers/{provider}/revoke", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'back': back})}", 303)
        elif await servers.store.revoke_provider(view.provider.id, admin):
            await apply()
            sessions.toast(request, "web.admin.toast.provider_revoke", name=name)
        else:
            sessions.toast(request, "web.admin.toast.failed", error=True)
        return RedirectResponse(back, 303)

    return router


def _sort_key(column: str, view: ProviderView) -> Any:
    match column:
        case "approved":
            return view.count("approved")
        case "waiting":
            return view.count("waiting")
        case "added":
            return view.history.added or datetime.min.replace(tzinfo=UTC)
        case _:
            return view.provider.name.casefold()
