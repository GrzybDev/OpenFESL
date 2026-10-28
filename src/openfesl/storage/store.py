from collections.abc import Collection, Sequence

from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.inventory import Ownership
from openfesl.core.store import NewProduct, Product
from openfesl.storage.models import StoreProductRow


class SqlProductStore:
    """The products of the games' stores in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def stock(self, game: str, products: Sequence[NewProduct]) -> bool:
        """Store a game's starting products, unless the game has products already.

        Stores on other servers take turns, so a game is stocked once.

        Args:
            game: The game's data-file name.
            products: The products, in the order they are listed.

        Returns:
            Whether they were stored.
        """
        async with self._sessions.begin() as session:
            await _lock(session, game)
            if await session.scalar(select(exists().where(StoreProductRow.game == game))):
                return False
            await session.execute(
                insert(StoreProductRow),
                [
                    {
                        "game": game,
                        "item_id": product.item_id,
                        "type": product.ownership.type,
                        "days": product.ownership.days,
                        "uses": product.ownership.uses,
                        "currency": product.currency,
                        "price": product.price,
                        "sort": sort,
                    }
                    for sort, product in enumerate(products)
                ],
            )
            return True

    async def withdraw(self, game: str, items: Collection[int]) -> int:
        """Remove the products of items from a game's store.

        Args:
            game: The game's data-file name.
            items: The items' numbers.

        Returns:
            How many products were removed.
        """
        async with self._sessions.begin() as session:
            removed = await session.scalars(
                delete(StoreProductRow)
                .where(StoreProductRow.game == game, StoreProductRow.item_id.in_(items))
                .returning(StoreProductRow.id)
            )
            return len(removed.all())

    async def products(self, game: str) -> list[Product]:
        """Return the products a game's store lists, in order.

        Args:
            game: The game's data-file name.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StoreProductRow)
                .where(
                    StoreProductRow.game == game,
                    StoreProductRow.published,
                    StoreProductRow.account.is_(False),
                )
                .order_by(StoreProductRow.sort, StoreProductRow.id)
            )
            return [_product(row) for row in rows]


async def _lock(session: AsyncSession, game: str) -> None:
    # Held until the transaction ends, on any server.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"store:{game}"))))


def _product(row: StoreProductRow) -> Product:
    return Product(
        id=row.id,
        item_id=row.item_id,
        ownership=Ownership(row.type, row.days, row.uses),
        currency=row.currency,
        price=row.price,
        original_price=row.original_price,
        sort=row.sort,
    )
