from collections.abc import Collection
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction, AuditEntry, AuditFilter, Auditor, LoggedAction
from openfesl.core.admins import Admin, Role
from openfesl.storage.models import AdminActionRow


def action_row(
    action: AdminAction, target: str, details: dict[str, Any], admin: Admin | None
) -> AdminActionRow:
    """Return the log's row of an action, for a store to add with the change it records.

    Args:
        action: What the admin did.
        target: What it was done to.
        details: What changed, as the action defines.
        admin: The admin who did it; None for the command line.
    """
    return AdminActionRow(
        admin=admin.subject if admin else None,
        admin_name=admin.name if admin else None,
        admin_role=admin.role.value if admin else None,
        action=action,
        target=target,
        details=details,
    )


class SqlAdminLog:
    """The log of the admin's actions in PostgreSQL, which the stores write with each change."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def record(self, action: AdminAction, target: str, admin: Admin | None) -> None:
        """Record an action that changes nothing stored, such as a kick, for the server to apply.

        Args:
            action: What the admin did.
            target: What it was done to.
            admin: The admin who did it; None for the command line.
        """
        async with self._sessions.begin() as session:
            session.add(action_row(action, target, {}, admin))

    async def entries(
        self, chosen: AuditFilter, offset: int, limit: int
    ) -> tuple[int, list[AuditEntry]]:
        """Return the actions a filter lets through, newest first, with how many there are.

        Args:
            chosen: Which actions.
            offset: How many to skip.
            limit: The most to return.
        """
        conditions = _conditions(chosen)
        async with self._sessions() as session:
            total = await session.scalar(
                select(func.count()).select_from(AdminActionRow).where(*conditions)
            )
            rows = await session.scalars(
                select(AdminActionRow)
                .where(*conditions)
                .order_by(AdminActionRow.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return total or 0, [_entry(row) for row in rows]

    async def auditors(self, chosen: AuditFilter) -> list[Auditor]:
        """Return who acted among the actions a filter lets through by anyone, most first.

        Args:
            chosen: Which actions; its admin is not applied.
        """
        anyone = AuditFilter(account=chosen.account, personas=chosen.personas)
        name = func.max(AdminActionRow.admin_name)
        role = func.max(AdminActionRow.admin_role)
        count = func.count()
        async with self._sessions() as session:
            rows = await session.execute(
                select(AdminActionRow.admin, name, role, count)
                .where(*_conditions(anyone))
                .group_by(AdminActionRow.admin)
                .order_by(count.desc(), name)
            )
            return [
                Auditor(subject, name, Role(role) if role else None, number)
                for subject, name, role, number in rows
            ]

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


# What the admin does to an account itself, and to its personas.
_ACCOUNT_ACTIONS = [
    AdminAction.ACCOUNT_STATE,
    AdminAction.ACCOUNT_VERIFIED,
    AdminAction.SESSION_END,
    AdminAction.LOGINS_FORGET,
    AdminAction.ENTITLEMENT_GRANT,
    AdminAction.ENTITLEMENT_STATE,
]
_PERSONA_ACTIONS = [AdminAction.PERSONA_STATE, AdminAction.PLAYER_KICK]


def _conditions(chosen: AuditFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [AdminActionRow.action.in_(list(AdminAction))]
    if chosen.admin is not None:
        conditions.append(
            AdminActionRow.admin.is_(None)
            if chosen.admin == ""
            else AdminActionRow.admin == chosen.admin
        )
    if chosen.account is not None:
        conditions.append(
            or_(
                and_(
                    AdminActionRow.action.in_(_ACCOUNT_ACTIONS),
                    AdminActionRow.target == str(chosen.account),
                ),
                and_(
                    AdminActionRow.action.in_(_PERSONA_ACTIONS),
                    AdminActionRow.target.in_(_texts(chosen.personas)),
                ),
            )
        )
    return conditions


def _texts(numbers: Collection[int]) -> list[str]:
    return [str(number) for number in numbers]


def _entry(row: AdminActionRow) -> AuditEntry:
    role = Role(row.admin_role) if row.admin_role else None
    return AuditEntry(
        row.id,
        row.at,
        row.admin,
        row.admin_name,
        role,
        AdminAction(row.action),
        row.target,
        row.details,
    )
