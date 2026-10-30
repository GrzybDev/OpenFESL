from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.box_tables import TableEntry
from openfesl.core.drops import Box
from openfesl.storage.admin import action_row
from openfesl.storage.models import StoreBoxRow, StoreDropRow, StoreProductRow
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

    async def save_table(
        self,
        box: Box,
        weights: Sequence[int],
        entries: Sequence[TableEntry],
        worth_rule: bool,
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Replace a box's drop table and record the change among the admin's actions.

        Args:
            box: The box, as it was.
            weights: The weight of each tier, by its number.
            entries: Its entries; those without a product add one, unpublished, at their price.
            worth_rule: Whether it promises that every entry is worth its price.
            details: What the admin's log keeps of the change.
            admin: The admin who saved it.
        """
        async with self._sessions.begin() as session:
            game = await session.scalar(select(StoreBoxRow.game).where(StoreBoxRow.id == box.id))
            assert game is not None
            numbers = [
                entry.product
                if entry.product is not None
                else await _product(session, game, box.currency, entry)
                for entry in entries
            ]
            await session.execute(delete(StoreDropRow).where(StoreDropRow.box_id == box.id))
            # A product that two tiers name is given by the first.
            drops = {
                number: entry.tier
                for number, entry in reversed(list(zip(numbers, entries, strict=True)))
            }
            if drops:
                await session.execute(
                    insert(StoreDropRow),
                    [
                        {
                            "box_id": box.id,
                            "product_id": number,
                            "tier": tier,
                            "weight": weights[tier],
                        }
                        for number, tier in drops.items()
                    ],
                )
            await session.execute(
                update(StoreBoxRow).where(StoreBoxRow.id == box.id).values(worth_rule=worth_rule)
            )
            session.add(action_row(AdminAction.BOX_TABLE, f"{game}:{box.key}", details, admin))


async def _product(session: AsyncSession, game: str, currency: str, entry: TableEntry) -> int:
    # The store's product of an entry, added unpublished at its price where the store lacks one.
    ownership = entry.ownership
    await session.execute(
        insert(StoreProductRow)
        .values(
            game=game,
            item_id=entry.item_id,
            type=ownership.type,
            days=ownership.days,
            uses=ownership.uses,
            currency=currency,
            price=entry.price,
            published=False,
        )
        .on_conflict_do_nothing()
    )
    number = await session.scalar(
        select(StoreProductRow.id).where(
            StoreProductRow.game == game,
            StoreProductRow.item_id == entry.item_id,
            StoreProductRow.type == ownership.type,
            StoreProductRow.days.is_not_distinct_from(ownership.days),
            StoreProductRow.uses.is_not_distinct_from(ownership.uses),
            StoreProductRow.currency == currency,
        )
    )
    assert number is not None
    return number
