from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.storage.admin import action_row
from openfesl.storage.models import DealArtRow


class SqlDealUploads:
    """The pictures that the admin uploaded for hot deals' offers, in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def picture(self, game: str, offer: str, big: bool) -> bytes | None:
        """Return an offer's uploaded picture, if one was uploaded.

        Args:
            game: The game's data-file name.
            offer: The offer's name.
            big: Whether it is the big picture rather than the small one.
        """
        async with self._sessions() as session:
            return await session.scalar(
                select(DealArtRow.image).where(
                    DealArtRow.game == game, DealArtRow.offer == offer, DealArtRow.big.is_(big)
                )
            )

    async def uploaded(self, game: str) -> dict[str, set[bool]]:
        """Return the offers of a game with uploaded pictures, each with which of them it has.

        Args:
            game: The game's data-file name.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(DealArtRow.offer, DealArtRow.big).where(DealArtRow.game == game)
            )
            found: dict[str, set[bool]] = {}
            for offer, big in rows:
                found.setdefault(offer, set()).add(big)
            return found

    async def put(self, game: str, offer: str, big: bool, image: bytes, admin: Admin) -> None:
        """Keep an offer's uploaded picture in place of its generated one, and record it.

        Args:
            game: The game's data-file name.
            offer: The offer's name.
            big: Whether it is the big picture rather than the small one.
            image: The picture, a PNG of the size the game shows.
            admin: The admin who uploaded it.
        """
        async with self._sessions.begin() as session:
            statement = insert(DealArtRow).values(game=game, offer=offer, big=big, image=image)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[DealArtRow.game, DealArtRow.offer, DealArtRow.big],
                    set_={"image": statement.excluded.image, "updated_at": func.now()},
                )
            )
            details = {"offer": offer, "big": big}
            session.add(action_row(AdminAction.DEAL_ART, game, details, admin))

    async def remove(self, game: str, offer: str, admin: Admin) -> bool:
        """Remove an offer's uploaded pictures, so the generated ones come back, and record it.

        Args:
            game: The game's data-file name.
            offer: The offer's name.
            admin: The admin who removed them.

        Returns:
            Whether it had any.
        """
        async with self._sessions.begin() as session:
            removed = await session.scalars(
                delete(DealArtRow)
                .where(DealArtRow.game == game, DealArtRow.offer == offer)
                .returning(DealArtRow.big)
            )
            if not removed.all():
                return False
            details = {"offer": offer, "removed": True}
            session.add(action_row(AdminAction.DEAL_ART, game, details, admin))
            return True
