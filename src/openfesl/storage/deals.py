from datetime import datetime

from sqlalchemy import exists, func, insert, select
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.calendar import Period
from openfesl.core.deal_picks import History, NewDeal
from openfesl.core.deals import Maker
from openfesl.core.inventory import Ownership
from openfesl.core.store import AUTO
from openfesl.storage.models import StoreBundleMemberRow, StoreBundleRow, StoreProductRow
from openfesl.storage.store import product_of

type Key = tuple[int, Ownership, str]


class SqlDealStore:
    """The bundles that the hot deals' rotation made, in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def generate(self, game: str, section: str, period: Period, make: Maker) -> None:
        """Store the bundles of a period of a section once, as the first to look makes them.

        Stores on other servers take turns, and a period that has bundles keeps them.

        Args:
            game: The game's data-file name.
            section: The section's name.
            period: The period.
            make: Makes the bundles from what the section must not repeat.
        """
        async with self._sessions.begin() as session:
            # Held until the transaction ends, on any server.
            lock = func.pg_advisory_xact_lock(func.hashtext(f"deals:{game}"))
            await session.execute(select(lock))
            made = exists().where(
                StoreBundleRow.game == game,
                StoreBundleRow.section == section,
                StoreBundleRow.source == AUTO,
                StoreBundleRow.starts_at == period.start,
            )
            if await session.scalar(select(made)):
                return
            history = await _history(session, game, section, period.start)
            deals = make(history)
            numbers = await _numbers(session, game, history, deals)
            for deal in deals:
                await _add(session, game, section, period, deal, numbers)


async def _history(session: AsyncSession, game: str, section: str, start: datetime) -> History:
    ours = (StoreBundleRow.game == game, StoreBundleRow.source == AUTO)
    running = (StoreBundleRow.starts_at <= start, StoreBundleRow.ends_at > start)
    others = (*ours, *running, StoreBundleRow.section != section)
    items = await session.scalars(
        select(StoreProductRow.item_id)
        .join(StoreBundleMemberRow, StoreBundleMemberRow.product_id == StoreProductRow.id)
        .join(StoreBundleRow, StoreBundleRow.id == StoreBundleMemberRow.bundle_id)
        .where(*others)
    )
    products = await session.scalars(select(StoreProductRow).where(StoreProductRow.game == game))
    return History(
        live_items=frozenset(items),
        products={
            (row.item_id, Ownership(row.type, row.days, row.uses), row.currency): product_of(row)
            for row in products
        },
    )


async def _numbers(
    session: AsyncSession, game: str, history: History, deals: list[NewDeal]
) -> dict[Key, int]:
    # The products' numbers, adding those the store lacks as parts of bundles alone.
    numbers = {key: product.id for key, product in history.products.items()}
    missing = {
        (product.item_id, product.ownership, product.currency): product
        for deal in deals
        for product in deal.products
        if (product.item_id, product.ownership, product.currency) not in numbers
    }
    if missing:
        await session.execute(
            upsert(StoreProductRow)
            .values(
                [
                    {
                        "game": game,
                        "item_id": product.item_id,
                        "type": product.ownership.type,
                        "days": product.ownership.days,
                        "uses": product.ownership.uses,
                        "currency": product.currency,
                        "price": product.price,
                        "published": False,
                    }
                    for product in missing.values()
                ]
            )
            .on_conflict_do_nothing()
        )
        # Including any that the store added meanwhile, which the insert left alone.
        added = await session.execute(
            select(StoreProductRow).where(
                StoreProductRow.game == game,
                StoreProductRow.item_id.in_({key[0] for key in missing}),
            )
        )
        for row in added.scalars():
            numbers[(row.item_id, Ownership(row.type, row.days, row.uses), row.currency)] = row.id
    return numbers


async def _add(
    session: AsyncSession,
    game: str,
    section: str,
    period: Period,
    deal: NewDeal,
    numbers: dict[Key, int],
) -> None:
    bundle = deal.bundle
    number = await session.scalar(
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
            starts_at=period.start,
            ends_at=period.end,
            section=section,
            pool=bundle.pool,
            source=AUTO,
        )
        .returning(StoreBundleRow.id)
    )
    members = {
        numbers[(product.item_id, product.ownership, product.currency)] for product in deal.products
    }
    await session.execute(
        insert(StoreBundleMemberRow),
        [{"bundle_id": number, "product_id": member} for member in sorted(members)],
    )
