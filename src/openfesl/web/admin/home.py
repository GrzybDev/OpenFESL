from datetime import UTC, datetime
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.games import Games
from openfesl.core.servers import Approval
from openfesl.web.admin.auth import AdminSessions


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
    accounts: AccountTotals,
    approval: Approval,
) -> APIRouter:
    """Return the admin panel's home, with the numbers of the instance and its hosting mode.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games the server serves.
        census: Counts the players and servers of each game.
        accounts: Counts the players' accounts.
        approval: Knows the hosting mode.
    """
    router = APIRouter()

    @router.get("/admin/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        now = datetime.now(UTC)
        activities = [census.activity(game) for game in games]
        total, new = await accounts.totals(now.replace(hour=0, minute=0, second=0, microsecond=0))
        return sessions.render(
            request,
            "admin.Home",
            admin=admin,
            online=sum(activity.online for activity in activities),
            hosting=sum(activity.servers for activity in activities),
            accounts=total,
            new_accounts=new,
            mode=(await approval.mode()).value,
        )

    return router
