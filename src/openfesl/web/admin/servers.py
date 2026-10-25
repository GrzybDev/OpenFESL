from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.census import Census, Server
from openfesl.core.games import Game, Games
from openfesl.core.servers import (
    Approval,
    Attempts,
    Decision,
    GameServer,
    ServerState,
    ServerStore,
)
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions


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


def decision_routes(sessions: AdminSessions, views: ServerViews) -> APIRouter:
    """Return the admin's decisions about dedicated servers, each confirmed where it asks to be.

    A server whose provider the admin does not know is approved only after a confirmation,
    which without scripts is a page of its own.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Puts together what the panel shows about the servers.
    """
    router = APIRouter()

    @router.get("/admin/servers/{ugid}/approve", response_class=HTMLResponse)
    async def confirm_approval(request: Request, ugid: str, back: str = "") -> HTMLResponse:
        admin = sessions.admin(request)
        view = await views.find(ugid)
        return sessions.render(
            request,
            "admin.Confirm",
            admin=admin,
            view=view,
            decision="approve",
            back=_back(request, back),
        )

    @router.post("/admin/servers/{ugid}/approve")
    async def approve(request: Request, ugid: str) -> Response:
        admin = sessions.admin(request)
        form = await request.form()
        sessions.check(request, form)
        view = await views.find(ugid)
        back = _back(request, str(form.get("back", "")))
        if view.server.provider is None and form.get("confirmed") != "1":
            target = localize(f"/admin/servers/{ugid}/approve", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'back': back})}", 303)
        if await views.store.set_state(ugid, ServerState.APPROVED, admin):
            sessions.toast(request, "web.admin.toast.approved", name=view.server.name)
        else:
            sessions.toast(request, "web.admin.toast.failed", error=True)
        return RedirectResponse(back, 303)

    return router


def _back(request: Request, back: str) -> str:
    # A decision returns to the panel's page it was made on, and never to another site.
    if back.startswith(("/admin/", "/pl/admin/")) and "//" not in back:
        return back
    return localize("/admin/", request.state.language)
