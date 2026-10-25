from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction, LoggedAction
from openfesl.storage.models import AdminActionRow


class SqlAdminLog:
    """The log of the admin's actions in PostgreSQL, which the stores write with each change."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def record(self, action: AdminAction, target: str, admin: str | None) -> None:
        """Record an action that changes nothing stored, such as a kick, for the server to apply.

        Args:
            action: What the admin did.
            target: What it was done to.
            admin: The admin who did it; None for the command line.
        """
        async with self._sessions.begin() as session:
            session.add(AdminActionRow(admin=admin, action=action, target=target, details={}))

    async def last(self) -> int:
        """Return the number of the latest action; 0 before the first."""
        async with self._sessions() as session:
            return await session.scalar(select(func.max(AdminActionRow.id))) or 0

    async def since(self, after: int) -> list[LoggedAction]:
        """Return the actions recorded after one, oldest first.

        Actions this version does not know, as those of a newer one, are left out.

        Args:
            after: The number of the last action already seen.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AdminActionRow)
                .where(AdminActionRow.id > after, AdminActionRow.action.in_(list(AdminAction)))
                .order_by(AdminActionRow.id)
            )
            return [
                LoggedAction(row.id, AdminAction(row.action), row.target, row.details)
                for row in rows
            ]
