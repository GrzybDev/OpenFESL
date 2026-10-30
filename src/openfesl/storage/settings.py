from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.storage.admin import action_row
from openfesl.storage.models import SettingRow


class SqlSettingStore:
    """The instance's settings in PostgreSQL, each change recorded among the admin's actions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def settings(self, prefix: str) -> dict[str, str]:
        """Return the settings whose keys start with a prefix, by key.

        Args:
            prefix: The start of the keys.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(SettingRow.key, SettingRow.value).where(
                    SettingRow.key.startswith(prefix, autoescape=True)
                )
            )
            return {key: value for key, value in rows}

    async def change(self, key: str, value: str | None, admin: Admin | None) -> None:
        """Set or remove a setting and record the change.

        Args:
            key: The setting's key.
            value: Its new value; None removes it.
            admin: The admin who changed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            await put_setting(session, key, value)
            session.add(action_row(AdminAction.SETTING, key, {"value": value}, admin))

    async def change_all(
        self,
        settings: Mapping[str, str | None],
        action: AdminAction,
        target: str,
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Set or remove settings together and record them as one action of the admin's.

        Args:
            settings: The settings' new values by key; None removes one.
            action: What the admin did.
            target: What it was done to.
            details: What the admin's log keeps of it.
            admin: The admin who changed them.
        """
        async with self._sessions.begin() as session:
            for key, value in settings.items():
                await put_setting(session, key, value)
            session.add(action_row(action, target, details, admin))


async def put_setting(session: AsyncSession, key: str, value: str | None) -> None:
    """Set or remove a setting within a transaction, which records the change itself.

    Args:
        session: The transaction's session.
        key: The setting's key.
        value: Its new value; None removes it.
    """
    if value is None:
        await session.execute(delete(SettingRow).where(SettingRow.key == key))
        return
    statement = insert(SettingRow).values(key=key, value=value)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[SettingRow.key], set_={"value": statement.excluded.value}
        )
    )
