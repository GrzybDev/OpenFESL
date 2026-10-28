from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, delete, exists, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.inventory import Ownership
from openfesl.core.store import (
    Bundle,
    ListedBundle,
    NewProduct,
    Product,
)
from openfesl.storage.models import (
    StoreBundleMemberRow,
    StoreBundleRow,
    StoreProductRow,
)


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

    async def product(self, game: str, number: int) -> Product | None:
        """Return a product a game's store sells to heroes, if it lists one by a number.

        Args:
            game: The game's data-file name.
            number: The product's number.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(StoreProductRow).where(
                    StoreProductRow.id == number,
                    StoreProductRow.game == game,
                    StoreProductRow.published,
                    StoreProductRow.account.is_(False),
                )
            )
            return _product(row) if row else None

    async def add_bundle(self, game: str, bundle: Bundle) -> int:
        """Add a bundle to a game's store.

        Args:
            game: The game's data-file name.
            bundle: The bundle, whose products the store holds.

        Returns:
            The bundle's number.
        """
        async with self._sessions.begin() as session:
            number: int | None = await session.scalar(
                insert(StoreBundleRow)
                .values(
                    game=game,
                    name=bundle.name,
                    description=bundle.description,
                    currency=bundle.currency,
                    price=bundle.price,
                    original_price=bundle.original_price,
                    faction=bundle.faction,
                    hero_class=bundle.hero_class,
                    game_order=bundle.game_order,
                    starts_at=bundle.starts,
                    ends_at=bundle.ends,
                )
                .returning(StoreBundleRow.id)
            )
            assert number is not None
            if bundle.members:
                await session.execute(
                    insert(StoreBundleMemberRow),
                    [{"bundle_id": number, "product_id": member} for member in bundle.members],
                )
            return number

    async def bundles(self, game: str) -> list[ListedBundle]:
        """Return the bundles a game's store sells now, in their order among the hot deals.

        Args:
            game: The game's data-file name.
        """
        return await self._bundles(StoreBundleRow.game == game)

    async def bundle(self, game: str, number: int) -> ListedBundle | None:
        """Return a bundle a game's store sells now, if it sells one by a number.

        Args:
            game: The game's data-file name.
            number: The bundle's number.
        """
        found = await self._bundles(StoreBundleRow.game == game, StoreBundleRow.id == number)
        return found[0] if found else None

    async def _bundles(self, *chosen: ColumnElement[bool]) -> list[ListedBundle]:
        now = func.now()
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(StoreBundleRow)
                    .where(
                        *chosen,
                        StoreBundleRow.published,
                        or_(StoreBundleRow.starts_at.is_(None), StoreBundleRow.starts_at <= now),
                        or_(StoreBundleRow.ends_at.is_(None), StoreBundleRow.ends_at > now),
                    )
                    .order_by(StoreBundleRow.game_order, StoreBundleRow.id)
                )
            )
            members = await session.execute(
                select(StoreBundleMemberRow.bundle_id, StoreProductRow)
                .join(StoreProductRow, StoreProductRow.id == StoreBundleMemberRow.product_id)
                .where(StoreBundleMemberRow.bundle_id.in_([row.id for row in rows]))
                .order_by(StoreProductRow.sort, StoreProductRow.id)
            )
        held: dict[int, list[Product]] = {}
        for bundle_id, product in members:
            held.setdefault(bundle_id, []).append(_product(product))
        return [
            ListedBundle(
                row.id,
                row.created_at,
                Bundle(
                    name=row.name,
                    description=row.description,
                    currency=row.currency,
                    price=row.price,
                    original_price=row.original_price,
                    faction=row.faction,
                    hero_class=row.hero_class,
                    game_order=row.game_order,
                    starts=row.starts_at,
                    ends=row.ends_at,
                    members=tuple(product.id for product in held.get(row.id, [])),
                ),
                held.get(row.id, []),
            )
            for row in rows
        ]


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
