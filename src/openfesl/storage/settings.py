from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.storage.models import AdminActionRow, SettingRow


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

    async def change(self, key: str, value: str | None, admin: str | None) -> None:
        """Set or remove a setting and record the change.

        Args:
            key: The setting's key.
            value: Its new value; None removes it.
            admin: The admin who changed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            if value is None:
                await session.execute(delete(SettingRow).where(SettingRow.key == key))
            else:
                statement = insert(SettingRow).values(key=key, value=value)
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[SettingRow.key], set_={"value": statement.excluded.value}
                    )
                )
            session.add(
                AdminActionRow(
                    admin=admin, action=AdminAction.SETTING, target=key, details={"value": value}
                )
            )
