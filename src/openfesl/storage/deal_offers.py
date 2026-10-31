from datetime import datetime
from typing import Any

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.store import ADMIN, Bundle, ListedBundle
from openfesl.storage.admin import action_row
from openfesl.storage.models import StoreBundleMemberRow, StoreBundleRow
from openfesl.storage.store import add_members, bundle_values, with_members


class SqlOfferStore:
    """The admin's own offers among the hot deals in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def offers(self, game: str, since: datetime) -> list[ListedBundle]:
        """Return a game's admin offers that end after a moment or never, the newest first.

        Args:
            game: The game's data-file name.
            since: The moment.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StoreBundleRow)
                .where(
                    StoreBundleRow.game == game,
                    StoreBundleRow.source == ADMIN,
                    StoreBundleRow.published,
                    or_(StoreBundleRow.ends_at.is_(None), StoreBundleRow.ends_at > since),
                )
                .order_by(StoreBundleRow.id.desc())
            )
            return await with_members(session, list(rows))

    async def offer(self, game: str, number: int) -> ListedBundle | None:
        """Return an admin offer of a game by its bundle number, whether it is sold now or not.

        Args:
            game: The game's data-file name.
            number: The offer's bundle number.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(StoreBundleRow).where(
                    StoreBundleRow.id == number,
                    StoreBundleRow.game == game,
                    StoreBundleRow.source == ADMIN,
                    StoreBundleRow.published,
                )
            )
            found = await with_members(session, [row] if row else [])
        return found[0] if found else None

    async def save(
        self,
        game: str,
        number: int | None,
        bundle: Bundle,
        action: AdminAction,
        details: dict[str, Any],
        admin: Admin,
    ) -> int | None:
        """Add an admin offer or change one, with its products, and record it, all or nothing.

        Args:
            game: The game's data-file name.
            number: The offer's bundle number; none for a new offer.
            bundle: The offer as it is to be sold.
            action: What the admin did.
            details: What the admin's log keeps of it.
            admin: The admin who did it.

        Returns:
            The offer's bundle number; none for a number that is no admin offer of the game.
        """
        values = bundle_values(bundle)
        async with self._sessions.begin() as session:
            if number is None:
                number = await session.scalar(
                    insert(StoreBundleRow).values(game=game, **values).returning(StoreBundleRow.id)
                )
            else:
                number = await session.scalar(
                    update(StoreBundleRow)
                    .where(
                        StoreBundleRow.id == number,
                        StoreBundleRow.game == game,
                        StoreBundleRow.source == ADMIN,
                    )
                    .values(**values)
                    .returning(StoreBundleRow.id)
                )
                if number is None:
                    return None
                await session.execute(
                    delete(StoreBundleMemberRow).where(StoreBundleMemberRow.bundle_id == number)
                )
            assert number is not None
            await add_members(session, number, bundle)
            session.add(action_row(action, f"{game}:{number}", details, admin))
            return int(number)
