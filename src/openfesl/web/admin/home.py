from datetime import UTC, datetime
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.games import Games
from openfesl.core.servers import HostingMode, ServerState
from openfesl.core.session import Players
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.servers import ServerViews


class AccountTotals(Protocol):
    """Counts the players' accounts."""

    async def totals(self, since: datetime) -> tuple[int, int]:
        """Return how many players' accounts there are, and how many of them are new.

        Args:
            since: Accounts created from then on count as new.
        """


def home_routes(
    sessions: AdminSessions,
    games: Games,
    census: Census,
    players: Players,
    accounts: AccountTotals,
    views: ServerViews,
) -> APIRouter:
    """Return the admin panel's home: what needs a decision, the instance's numbers and its mode.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games the server serves.
        census: Counts the players and servers of each game.
        players: Who is logged in.
        accounts: Counts the players' accounts.
        views: Puts together what the panel shows about the dedicated servers.
    """
    router = APIRouter()

    @router.get("/admin/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        now = datetime.now(UTC)
        activities = [census.activity(game) for game in games]
        total, new = await accounts.totals(now.replace(hour=0, minute=0, second=0, microsecond=0))
        mode = await views.store.hosting_mode()
        servers = await views.all()
        # In the open mode nothing waits: every server lists its game at once.
        waiting = [
            view
            for view in servers
            if mode is HostingMode.APPROVED
            and view.server.state is ServerState.PENDING
            and not view.server.revoked
        ]
        warnings = [view for view in servers if view.server.revoked and view.attempts.refused_today]
        return sessions.render(
            request,
            "admin.Home",
            admin=admin,
            online=sum(activity.online for activity in activities),
            logged_in=players.logged_in(),
            hosting=sum(activity.servers for activity in activities),
            accounts=total,
            new_accounts=new,
            mode=mode.value,
            waiting=waiting,
            warnings=warnings,
        )

    return router
