from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.storage.admin import action_row
from openfesl.storage.models import SettingRow

# The settings that say whether each game shows its own server mark, by the game's id after it.
PREFIX = "mark."


class SqlMarkStore:
    """Whether each game shows its own server mark, in PostgreSQL's settings, each change logged."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def hidden_marks(self) -> set[str]:
        """Return the ids of the games whose own server mark an admin turned off."""
        async with self._sessions() as session:
            rows = await session.execute(
                select(SettingRow.key).where(
                    SettingRow.key.startswith(PREFIX, autoescape=True), SettingRow.value == "off"
                )
            )
            return {key.removeprefix(PREFIX) for key in rows.scalars()}

    async def show_mark(self, game: str, shown: bool, official: int, admin: Admin) -> None:
        """Keep whether a game shows its own server mark and record the change.

        Args:
            game: The game's id.
            shown: Whether official servers publish the mark.
            official: How many official servers host the game now, as the log records.
            admin: The admin who changed it.
        """
        statement = insert(SettingRow).values(key=PREFIX + game, value="on" if shown else "off")
        details = {"shown": shown, "official": official}
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SettingRow.key], set_={"value": statement.excluded.value}
                )
            )
            session.add(action_row(AdminAction.GAME_MARK, game, details, admin))
