from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.store import Product
from openfesl.storage.models import StoreProductRow
from openfesl.storage.store import product_of


class SqlPriceStore:
    """The price points of the games' stores in PostgreSQL, as the admin panel shows them."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def every(self, game: str) -> list[Product]:
        """Return every product of a game's store for heroes, published or not, in order.

        Args:
            game: The game's data-file name.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StoreProductRow)
                .where(StoreProductRow.game == game, StoreProductRow.account.is_(False))
                .order_by(StoreProductRow.sort, StoreProductRow.id)
            )
            return [product_of(row) for row in rows]
