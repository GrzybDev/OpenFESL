from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.drops import Box
from openfesl.storage.models import StoreBoxRow
from openfesl.storage.store import boxes_of


class SqlBoxStore:
    """The boxes of the games' stores and their drop tables in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def boxes(self, game: str) -> list[Box]:
        """Return every box of a game's store with its drops, boxes before chests.

        Args:
            game: The game's data-file name.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StoreBoxRow)
                .where(StoreBoxRow.game == game)
                .order_by(StoreBoxRow.kind, StoreBoxRow.id)
            )
            return await boxes_of(session, list(rows))
