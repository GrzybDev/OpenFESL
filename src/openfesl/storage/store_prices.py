from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.store import Product
from openfesl.core.store_prices import PointChange, PriceRefusal, PriceRefused
from openfesl.storage.admin import action_row
from openfesl.storage.models import StoreProductRow
from openfesl.storage.settings import put_setting
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

    async def save_points(
        self, game: str, item_id: int, points: Sequence[PointChange], reset: bool, admin: Admin
    ) -> bool:
        """Save an item's price points and record what changed among the admin's actions.

        Args:
            game: The game's data-file name.
            item_id: The item's number, an ability's first level for all of its levels.
            points: Its price points, those to add among them.
            reset: Whether they are its rule's, as the admin reset them.
            admin: The admin who saved them.

        Returns:
            Whether anything changed.

        Raises:
            PriceRefused: When a point to add is one the store has, also at the same time.
        """
        async with self._sessions.begin() as session:
            changed = await _save(session, game, points)
            if not changed:
                return False
            details: dict[str, Any] = {"game": game, "item": item_id, "points": changed}
            if reset:
                details["reset"] = True
            session.add(action_row(AdminAction.STORE_PRICES, f"{game}:{item_id}", details, admin))
        return True

    async def apply_rule(
        self,
        game: str,
        settings: Mapping[str, str | None],
        points: Sequence[PointChange],
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Change a price rule and the price points it gives, and record it as one action.

        Args:
            game: The game's data-file name.
            settings: The settings that override the rule's fields, by key; None removes one.
            points: The price points the rule changes, those to add among them.
            details: What the admin's log keeps of the change: the rule and what it changed.
            admin: The admin who applied it.
        """
        async with self._sessions.begin() as session:
            for key, value in settings.items():
                await put_setting(session, key, value)
            await _save(session, game, points)
            target = f"{game}:{details['rule']}"
            session.add(action_row(AdminAction.STORE_RULE, target, details, admin))


async def _save(
    session: AsyncSession, game: str, points: Sequence[PointChange]
) -> list[dict[str, Any]]:
    # Writes price points within a transaction and returns what changed of each.
    changed: list[dict[str, Any]] = []
    numbers = [point.product for point in points if point.product is not None]
    rows = {
        row.id: row
        for row in await session.scalars(
            select(StoreProductRow).where(
                StoreProductRow.game == game, StoreProductRow.id.in_(numbers)
            )
        )
    }
    for point in points:
        row = rows.get(point.product) if point.product is not None else None
        if row is None or (row.price, row.published) == (point.price, point.published):
            continue
        changed.append(_change(row.id, point, row.price, row.published))
        row.price, row.published = point.price, point.published
    added = [point for point in points if point.product is None]
    if not added:
        return changed
    last = await session.scalar(
        select(func.max(StoreProductRow.sort)).where(StoreProductRow.game == game)
    )
    try:
        async with session.begin_nested():
            for sort, point in enumerate(added, (last or 0) + 1):
                number = await session.scalar(
                    insert(StoreProductRow)
                    .values(
                        game=game,
                        item_id=point.item_id,
                        type=point.ownership.type,
                        days=point.ownership.days,
                        uses=point.ownership.uses,
                        currency=point.currency,
                        price=point.price,
                        published=point.published,
                        sort=sort,
                    )
                    .returning(StoreProductRow.id)
                )
                assert number is not None
                changed.append(_change(number, point, None, False))
    except IntegrityError:
        raise PriceRefused(PriceRefusal.TWICE) from None
    return changed


def _change(number: int, point: PointChange, price: int | None, sold: bool) -> dict[str, Any]:
    # A price point's change as the admin's log keeps it.
    return {
        "product": number,
        "currency": point.currency,
        "ownership": point.ownership.written,
        "from": price,
        "to": point.price,
        "was": sold,
        "sold": point.published,
    }
