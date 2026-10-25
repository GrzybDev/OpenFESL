from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException

from openfesl.core.census import Census, Server
from openfesl.core.games import Game, Games
from openfesl.core.servers import (
    Approval,
    Attempts,
    Decision,
    GameServer,
    HostingMode,
    ServerState,
    ServerStore,
)
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.servers import label_name

# The tabs of the servers page with their sortable columns, the first the default order.
TABS = {
    "waiting": ("seen", "name", "provider", "map", "attempts"),
    "approved": ("name", "provider", "map", "decided", "players"),
    "revoked": ("decided", "name", "provider", "last", "refused"),
}
# Times and counts run from the highest down at first, names from A.
DESCENDING = {"seen", "attempts", "decided", "players", "last", "refused"}
# Each decision: the tabs whose servers it applies to, and the state it gives them.
DECISIONS = {
    "approve": ({"waiting", "revoked"}, ServerState.APPROVED),
    "reject": ({"waiting"}, ServerState.REVOKED),
    "revoke": ({"approved"}, ServerState.REVOKED),
}
_EARLIEST = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ServerView:
    """A dedicated server as the admin panel lists it.

    Attributes:
        server: The server.
        game: The game it runs, when known.
        hosting: The game it hosts now, also while players do not see it.
        attempts: How often it tried to host lately while players did not see it.
        decision: The admin's latest decision about it, if any.
    """

    server: GameServer
    game: Game | None
    hosting: Server | None
    attempts: Attempts
    decision: Decision | None

    @property
    def tab(self) -> str:
        """The tab of the servers page that lists it: waiting, approved or revoked."""
        if self.server.revoked:
            return "revoked"
        return "approved" if self.server.state is ServerState.APPROVED else "waiting"


class ServerViews:
    """Puts together what the admin panel shows about the dedicated servers."""

    def __init__(
        self, games: Games, census: Census, approval: Approval, store: ServerStore
    ) -> None:
        """Gather from the live games, the approval and the stored servers.

        Args:
            games: The games the server serves.
            census: Knows the games the servers host now.
            approval: Knows how often servers tried to host.
            store: Where the servers and the admin's decisions are kept.
        """
        self._games = list(games)
        self._census = census
        self._approval = approval
        self.store = store

    async def all(self) -> list[ServerView]:
        """Return every server that ever hosted, newest first."""
        servers = await self.store.all()
        return await self.views(reversed(servers))

    async def waiting(self, views: Iterable[ServerView]) -> list[ServerView]:
        """Return the servers among some that wait for a decision, in their order.

        Args:
            views: The servers.
        """
        # In the open mode nothing waits: every server lists its game at once.
        if await self.store.hosting_mode() is HostingMode.OPEN:
            return []
        return [view for view in views if view.tab == "waiting"]

    async def menu_counts(self) -> dict[str, int]:
        """Return the numbers that the admin panel's menu shows by its items."""
        if await self.store.hosting_mode() is HostingMode.OPEN:
            return {}
        servers = await self.store.all()
        return {
            "servers": sum(
                server.state is ServerState.PENDING and not server.revoked for server in servers
            )
        }

    async def find(self, ugid: str) -> ServerView:
        """Return one server.

        Args:
            ugid: The server's persistent game id.

        Raises:
            HTTPException: 404 when no server has that id.
        """
        found = await self.store.find([ugid])
        if not found:
            raise HTTPException(404)
        return (await self.views(found))[0]

    async def views(self, servers: Iterable[GameServer]) -> list[ServerView]:
        """Return the views of some servers, in their order.

        Args:
            servers: The servers.
        """
        listed = list(servers)
        decisions = await self.store.decisions([server.ugid for server in listed])
        views = []
        for server in listed:
            hosting, game = None, self._games[0] if len(self._games) == 1 else None
            for candidate in self._games:
                if (found := self._census.hosted_by(candidate, server.ugid)) is not None:
                    hosting, game = found, candidate
            attempts = self._approval.attempts(server.ugid)
            views.append(ServerView(server, game, hosting, attempts, decisions.get(server.ugid)))
        return views


@dataclass(frozen=True, slots=True)
class ServerList:
    """Which servers the servers page lists and how, as its address says.

    Attributes:
        tab: The tab: waiting, approved or revoked.
        query: Part of a server's name or the start of its id.
        sort: The column the list is sorted by.
        descending: Whether the list runs from the highest value down.
        open: The server whose details are open, by its id.
    """

    tab: str = "waiting"
    query: str = ""
    sort: str = "seen"
    descending: bool = True
    open: str | None = None

    @classmethod
    def read(cls, query: QueryParams, tab: str) -> Self:
        """Read the list's address; what it does not name is the tab's default.

        Args:
            query: The address's query.
            tab: The tab shown, which the address may not name.
        """
        sort = query.get("sort", "")
        if sort not in TABS[tab]:
            sort = TABS[tab][0]
        order = query.get("order", "")
        return cls(
            tab=tab,
            query=query.get("q", "").strip(),
            sort=sort,
            descending=order == "desc" if order in ("asc", "desc") else sort in DESCENDING,
            open=query.get("open") or None,
        )

    def admits(self, view: ServerView) -> bool:
        """Return whether a server matches the search, whatever its tab.

        Args:
            view: The server.
        """
        wanted = self.query.casefold()
        return wanted in view.server.name.casefold() or view.server.ugid.startswith(wanted)

    def changed(self, **changes: Any) -> ServerList:
        """Return the list with some choices changed and no details open.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **({"open": None} | changes))

    def switched(self, tab: str) -> ServerList:
        """Return the list in another tab, in that tab's own order, keeping the search.

        Args:
            tab: The tab.
        """
        return ServerList(
            tab=tab, query=self.query, sort=TABS[tab][0], descending=TABS[tab][0] in DESCENDING
        )

    def sorted_by(self, column: str) -> ServerList:
        """Return the list sorted by a column, the other way round when it already is.

        Args:
            column: The column.
        """
        descending = not self.descending if column == self.sort else column in DESCENDING
        return self.changed(sort=column, descending=descending)

    def link(self) -> str:
        """Return the query of the list's address, with a leading ?."""
        fields = [
            ("state", self.tab),
            ("q", self.query),
            ("sort", "" if self.sort == TABS[self.tab][0] else self.sort),
            ("order", "" if self.descending == (self.sort in DESCENDING) else self.direction),
            ("open", self.open or ""),
        ]
        return "?" + urlencode([(key, value) for key, value in fields if value])

    @property
    def direction(self) -> str:
        """The sort order as the address names it."""
        return "desc" if self.descending else "asc"


def server_list_routes(sessions: AdminSessions, views: ServerViews) -> APIRouter:
    """Return the servers page, with its tabs, search and details, and each server's address.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Puts together what the panel shows about the servers.
    """
    router = APIRouter()

    @router.get("/admin/servers", response_class=HTMLResponse)
    async def servers(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        every = await views.all()
        query = request.query_params
        searched = ServerList(query=query.get("q", "").strip())
        matching = [view for view in every if searched.admits(view)]
        counts = {tab: sum(view.tab == tab for view in matching) for tab in TABS}
        tab = query.get("state", "")
        if tab not in TABS:
            # A search opens the first tab it finds servers in.
            tab = next((name for name in TABS if searched.query and counts[name]), "waiting")
        listing = ServerList.read(query, tab)
        language: str = request.state.language
        found = sorted(
            (view for view in matching if view.tab == tab),
            key=lambda view: _sort_key(listing.sort, view, language),
            reverse=listing.descending,
        )
        # A provider's approved servers tell the admin who runs a server waiting for a decision.
        fellows: dict[bytes, list[str]] = {}
        for view in every:
            if view.tab == "approved" and view.server.provider is not None:
                fellows.setdefault(view.server.provider.id, []).append(view.server.name)
        return await sessions.render(
            request,
            "admin.Servers",
            admin=admin,
            listing=listing,
            servers=found,
            counts=counts,
            total=len(every),
            mode=(await views.store.hosting_mode()).value,
            fellows=fellows,
        )

    @router.get("/admin/servers/{ugid}")
    async def server(request: Request, ugid: str) -> Response:
        sessions.admin(request)
        view = await views.find(ugid)
        listing = replace(ServerList().switched(view.tab), open=ugid)
        target = localize("/admin/servers", request.state.language)
        return RedirectResponse(f"{target}{listing.link()}#row-{ugid}", 303)

    return router


def decision_routes(
    sessions: AdminSessions, views: ServerViews, apply: Callable[[], Awaitable[None]]
) -> APIRouter:
    """Return the admin's decisions about dedicated servers, each confirmed where it asks to be.

    Approving a server of a known provider is one step; approving one of an unknown provider or
    one that was revoked, rejecting and revoking ask first, which without scripts is a page of
    its own.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Puts together what the panel shows about the servers.
        apply: Lists, hides or ends the servers' games as the admin decided.
    """
    router = APIRouter()
    for decision in DECISIONS:
        path = f"/admin/servers/{{ugid}}/{decision}"
        router.add_api_route(
            path, _confirmation(sessions, views, decision), response_class=HTMLResponse
        )
        router.add_api_route(path, _decide(sessions, views, decision, apply), methods=["POST"])
    return router


def asks_first(decision: str, view: ServerView) -> bool:
    """Return whether a decision about a server asks for confirmation first.

    Args:
        decision: approve, reject or revoke.
        view: The server.
    """
    return decision != "approve" or view.server.provider is None or view.tab == "revoked"


def _confirmation(
    sessions: AdminSessions, views: ServerViews, decision: str
) -> Callable[[Request, str, str], Awaitable[HTMLResponse]]:
    async def confirm(request: Request, ugid: str, back: str = "") -> HTMLResponse:
        admin = sessions.admin(request)
        view = await views.find(ugid)
        return await sessions.render(
            request,
            "admin.Confirm",
            admin=admin,
            view=view,
            decision=decision,
            back=back_to(request, back or _tab_address(request, view)),
        )

    return confirm


def _decide(
    sessions: AdminSessions,
    views: ServerViews,
    decision: str,
    apply: Callable[[], Awaitable[None]],
) -> Callable[[Request, str], Awaitable[Response]]:
    tabs, state = DECISIONS[decision]

    async def decide(request: Request, ugid: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await views.find(ugid)
        back = back_to(request, str(form.get("back", "")))
        name = view.server.name
        provider = view.server.provider
        if view.tab not in tabs or (provider is not None and provider.revoked):
            # Another admin decided first, or the provider's revocation keeps it refused.
            sessions.toast(request, "web.admin.toast.unchanged", error=True, name=name)
        elif asks_first(decision, view) and form.get("confirmed") != "1":
            target = localize(f"/admin/servers/{ugid}/{decision}", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'back': back})}", 303)
        elif await views.store.set_state(ugid, state, admin):
            await apply()
            sessions.toast(request, f"web.admin.toast.{decision}", name=name)
        else:
            sessions.toast(request, "web.admin.toast.failed", error=True)
        return RedirectResponse(back, 303)

    return decide


def _tab_address(request: Request, view: ServerView) -> str:
    listing = ServerList().switched(view.tab)
    return localize(f"/admin/servers{listing.link()}", request.state.language)


def _sort_key(column: str, view: ServerView, language: str) -> Any:
    server, hosting, game = view.server, view.hosting, view.game
    match column:
        case "name":
            return server.name.casefold()
        case "provider":
            return server.provider.name.casefold() if server.provider else ""
        case "map":
            if hosting is None or game is None:
                return ""
            return label_name(game, "map", hosting.map, language).casefold()
        case "seen":
            return server.first_seen or _EARLIEST
        case "attempts":
            return view.attempts.last_hour
        case "decided":
            return view.decision.at if view.decision else _EARLIEST
        case "players":
            return hosting.players if hosting else -1
        case "last":
            return view.attempts.last or _EARLIEST
        case _:
            return view.attempts.refused_today
