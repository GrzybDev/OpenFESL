from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.game_art import ArtViewers
from openfesl.storage.admin import action_row
from openfesl.storage.models import SettingRow

# The settings that say who sees each game's art, by the game's id after it.
PREFIX = "art."


class SqlArtStore:
    """Who sees each game's art in PostgreSQL's settings, each choice recorded in the log."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def art_viewers(self) -> dict[str, ArtViewers]:
        """Return who sees each game's art, by the game's id, for the games an admin chose for.

        A value this version does not know, as a newer one's, is left out.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(SettingRow.key, SettingRow.value).where(
                    SettingRow.key.startswith(PREFIX, autoescape=True)
                )
            )
            return {
                key.removeprefix(PREFIX): ArtViewers(value)
                for key, value in rows
                if value in set(ArtViewers)
            }

    async def choose_art_viewers(
        self, game: str, viewers: ArtViewers, covers: Mapping[str, int], admin: Admin | None
    ) -> None:
        """Keep who sees a game's art and record the choice.

        Args:
            game: The game's id.
            viewers: Who sees it.
            covers: How many files of each kind the choice covers now, as the log records them.
            admin: The admin who chose; None for the command line.
        """
        statement = insert(SettingRow).values(key=PREFIX + game, value=viewers.value)
        details = {"viewers": viewers.value, **covers}
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SettingRow.key], set_={"value": statement.excluded.value}
                )
            )
            session.add(action_row(AdminAction.CATALOGUE_ART, game, details, admin))
