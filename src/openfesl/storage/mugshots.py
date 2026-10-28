from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.storage.models import MugshotRow


class SqlMugshotStore:
    """Mugshots of heroes in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def replace(self, persona_id: int, image: bytes) -> int:
        """Keep a hero's new mugshot in place of the one before.

        Args:
            persona_id: The hero.
            image: The mugshot as a PNG picture.

        Returns:
            The mugshot's version, which each different picture raises.
        """
        upsert = insert(MugshotRow).values(persona_id=persona_id, version=1, image=image)
        async with self._sessions.begin() as session:
            changed: int | None = await session.scalar(
                upsert.on_conflict_do_update(
                    index_elements=[MugshotRow.persona_id],
                    set_={
                        "version": MugshotRow.version + 1,
                        "image": image,
                        "updated_at": func.now(),
                    },
                    # The game uploads the same picture again and again, which keeps its version.
                    where=MugshotRow.image != upsert.excluded.image,
                ).returning(MugshotRow.version)
            )
            if changed is not None:
                return changed
            kept = select(MugshotRow.version).where(MugshotRow.persona_id == persona_id)
            return (await session.execute(kept)).scalar_one()
