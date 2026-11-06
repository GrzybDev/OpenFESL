from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.box_tables import TableEntry
from openfesl.core.drops import Box
from openfesl.core.store import NewProduct
from openfesl.storage.admin import action_row
from openfesl.storage.models import StoreBoxRow, StoreDropRow, StoreProductRow
from openfesl.storage.store import add_drops, boxes_of, product_values


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
        rare: Collection[int] = (),
    ) -> None:
        """Replace a box's drop table and record the change among the admin's actions.

        Args:
            box: The box, as it was.
            weights: The weight of each tier, by its number.
            entries: Its entries; those without a product add one, unpublished, at their price.
            worth_rule: Whether it promises that every entry is worth its price.
            details: What the admin's log keeps of the change.
            admin: The admin who saved it.
            rare: The numbers of the tiers whose drops are rare, as the Claw marks them.
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
            drops = [(entry.tier, number) for entry, number in zip(entries, numbers, strict=True)]
            await add_drops(session, box.id, drops, weights, rare)
            await session.execute(
                update(StoreBoxRow).where(StoreBoxRow.id == box.id).values(worth_rule=worth_rule)
            )
            action = AdminAction.CLAW_TABLE if box.kind == "claw" else AdminAction.BOX_TABLE
            session.add(action_row(action, f"{game}:{box.key}", details, admin))

    async def switch(self, game: str, box: Box, enabled: bool, admin: Admin) -> None:
        """Put a box on sale or take it off, or let keys open a chest or not, and record it.

        Args:
            game: The game's data-file name.
            box: The box or chest.
            enabled: Whether it is sold, or for a chest whether keys open it.
            admin: The admin who switched it.
        """
        details = {"game": game, "box": box.key, "kind": box.kind, "enabled": enabled}
        async with self._sessions.begin() as session:
            await session.execute(
                update(StoreBoxRow).where(StoreBoxRow.id == box.id).values(enabled=enabled)
            )
            session.add(action_row(AdminAction.BOX_SWITCH, f"{game}:{box.key}", details, admin))


async def _product(session: AsyncSession, game: str, currency: str, entry: TableEntry) -> int:
    # The store's product of an entry, added unpublished at its price where the store lacks one.
    ownership = entry.ownership
    assert entry.price is not None
    new = NewProduct(entry.item_id, ownership, currency, entry.price)
    await session.execute(
        insert(StoreProductRow).values(product_values(game, new)).on_conflict_do_nothing()
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
