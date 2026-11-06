from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, exists, func, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.drops import Box, Drop
from openfesl.core.inventory import Ownership
from openfesl.core.store import (
    Bundle,
    ListedBundle,
    NewBox,
    NewProduct,
    Product,
)
from openfesl.storage.models import (
    StoreBoxRow,
    StoreBundleMemberRow,
    StoreBundleRow,
    StoreDropRow,
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

    async def stock(
        self, game: str, products: Sequence[NewProduct], boxes: Sequence[NewBox]
    ) -> bool:
        """Store a game's starting products and boxes, unless the game has products already.

        Stores on other servers take turns, so a game is stocked once. A box that the game's data
        adds later joins a stocked store once, with the products its drops need, unpublished.

        Args:
            game: The game's data-file name.
            products: The products, in the order they are listed.
            boxes: The boxes, whose drops are among the products.

        Returns:
            Whether they were stored.
        """
        async with self._sessions.begin() as session:
            await _lock(session, game)
            if await session.scalar(select(exists().where(StoreProductRow.game == game))):
                await _add_boxes(session, game, boxes)
                return False
            await session.execute(
                insert(StoreProductRow),
                [
                    product_values(game, product)
                    | {"published": product.published, "account": product.account, "sort": sort}
                    for sort, product in enumerate(products)
                ],
            )
            if boxes:
                await _stock_boxes(session, game, boxes)
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
        return await self._published(game, StoreProductRow.account.is_(False))

    async def product(self, game: str, number: int, account: bool = False) -> Product | None:
        """Return a product a game's store sells, if it lists one by a number.

        Args:
            game: The game's data-file name.
            number: The product's number.
            account: Whether it is sold to accounts rather than to heroes.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(StoreProductRow).where(
                    StoreProductRow.id == number,
                    StoreProductRow.game == game,
                    StoreProductRow.published,
                    StoreProductRow.account.is_(account),
                )
            )
            return product_of(row) if row else None

    async def account_product(self, game: str, item_id: int) -> Product | None:
        """Return the first product of an item that a game's store sells accounts, if any.

        Args:
            game: The game's data-file name.
            item_id: The item's number.
        """
        sold = await self._published(
            game, StoreProductRow.item_id == item_id, StoreProductRow.account.is_(True)
        )
        return sold[0] if sold else None

    async def bundles(self, game: str, now: datetime) -> list[ListedBundle]:
        """Return the bundles a game's store sells at a moment, by their order among the hot deals.

        Args:
            game: The game's data-file name.
            now: The moment.
        """
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(StoreBundleRow)
                    .where(
                        StoreBundleRow.game == game,
                        StoreBundleRow.published,
                        or_(StoreBundleRow.starts_at.is_(None), StoreBundleRow.starts_at <= now),
                        or_(StoreBundleRow.ends_at.is_(None), StoreBundleRow.ends_at > now),
                        # A box taken off sale takes its hot deals with it.
                        or_(
                            StoreBundleRow.box_id.is_(None),
                            StoreBundleRow.box_id.in_(
                                select(StoreBoxRow.id).where(StoreBoxRow.enabled)
                            ),
                        ),
                    )
                    .order_by(StoreBundleRow.game_order, StoreBundleRow.id)
                )
            )
            return await with_members(session, rows)

    async def box(self, game: str, number: int) -> Box | None:
        """Return a box of a game's store with its drops, if it has one by a number.

        Args:
            game: The game's data-file name.
            number: The box's number.
        """
        return await self._box(StoreBoxRow.game == game, StoreBoxRow.id == number)

    async def box_by_key(self, game: str, key: str) -> Box | None:
        """Return a box of a game's store with its drops, if it has one by a key.

        Args:
            game: The game's data-file name.
            key: The key the game's data knows the box by.
        """
        return await self._box(StoreBoxRow.game == game, StoreBoxRow.key == key)

    async def of_item(self, game: str, item_id: int) -> list[Product]:
        """Return the products by which a game's store sells an item alone to heroes, in order.

        Args:
            game: The game's data-file name.
            item_id: The item's number.
        """
        return await self._published(
            game, StoreProductRow.item_id == item_id, StoreProductRow.account.is_(False)
        )

    async def bundle(self, game: str, number: int) -> ListedBundle | None:
        """Return a bundle of a game's store by its number, whether it is sold now or not.

        Args:
            game: The game's data-file name.
            number: The bundle's number.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(StoreBundleRow).where(
                    StoreBundleRow.id == number,
                    StoreBundleRow.game == game,
                    StoreBundleRow.published,
                )
            )
            found = await with_members(session, [row] if row else [])
        return found[0] if found else None

    async def _published(self, game: str, *chosen: ColumnElement[bool]) -> list[Product]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StoreProductRow)
                .where(StoreProductRow.game == game, StoreProductRow.published, *chosen)
                .order_by(StoreProductRow.sort, StoreProductRow.id)
            )
            return [product_of(row) for row in rows]

    async def _box(self, *chosen: ColumnElement[bool]) -> Box | None:
        async with self._sessions() as session:
            row = await session.scalar(select(StoreBoxRow).where(*chosen))
            found = await boxes_of(session, [row] if row else [])
        return found[0] if found else None


async def boxes_of(session: AsyncSession, rows: list[StoreBoxRow]) -> list[Box]:
    """Return boxes as the store's rows keep them, each with its drops and fallback.

    Args:
        session: The session that read the rows.
        rows: The boxes' rows.
    """
    drops = await session.execute(
        select(
            StoreDropRow.box_id,
            StoreDropRow.tier,
            StoreDropRow.weight,
            StoreDropRow.rare,
            StoreProductRow,
        )
        .join(StoreProductRow, StoreProductRow.id == StoreDropRow.product_id)
        .where(StoreDropRow.box_id.in_([row.id for row in rows]))
        .order_by(StoreDropRow.tier, StoreProductRow.sort, StoreProductRow.id)
    )
    held: dict[int, list[Drop]] = {}
    for box_id, tier, weight, rare, product in drops:
        held.setdefault(box_id, []).append(Drop(tier, weight, product_of(product), rare))
    numbers = [row.fallback_id for row in rows if row.fallback_id]
    fallbacks = {
        product.id: product_of(product)
        for product in await session.scalars(
            select(StoreProductRow).where(StoreProductRow.id.in_(numbers))
        )
    }
    return [
        Box(
            id=row.id,
            key=row.key,
            kind=row.kind,
            value=row.value,
            worth_rule=row.worth_rule,
            faction=row.faction,
            hero_class=row.hero_class,
            currency=row.currency,
            drops=tuple(held.get(row.id, [])),
            fallback=fallbacks.get(row.fallback_id) if row.fallback_id else None,
            enabled=row.enabled,
        )
        for row in rows
    ]


async def with_members(session: AsyncSession, rows: list[StoreBundleRow]) -> list[ListedBundle]:
    """Return bundles as their rows keep them, each with its products.

    Args:
        session: The session that read the rows.
        rows: The bundles' rows.
    """
    members = await session.execute(
        select(StoreBundleMemberRow.bundle_id, StoreProductRow)
        .join(StoreProductRow, StoreProductRow.id == StoreBundleMemberRow.product_id)
        .where(StoreBundleMemberRow.bundle_id.in_([row.id for row in rows]))
        .order_by(StoreProductRow.sort, StoreProductRow.id)
    )
    held: dict[int, list[Product]] = {}
    for bundle_id, product in members:
        held.setdefault(bundle_id, []).append(product_of(product))
    return [_listed(row, held.get(row.id, [])) for row in rows]


def _listed(row: StoreBundleRow, members: list[Product]) -> ListedBundle:
    return ListedBundle(
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
            discount=row.discount,
            starts=row.starts_at,
            ends=row.ends_at,
            members=tuple(product.id for product in members),
            box=row.box_id,
            section=row.section,
            pool=row.pool,
            source=row.source,
            texts={language: (texts[0] or "", texts[1]) for language, texts in row.texts.items()},
            stock=row.stock,
            sold=row.sold,
        ),
        members,
    )


def product_values(game: str, product: NewProduct) -> dict[str, Any]:
    """Return the columns of a new product, unpublished.

    Args:
        game: The game's data-file name.
        product: The product.
    """
    return {
        "game": game,
        "item_id": product.item_id,
        "type": product.ownership.type,
        "days": product.ownership.days,
        "uses": product.ownership.uses,
        "currency": product.currency,
        "price": product.price,
        "published": False,
    }


async def add_bundle(session: AsyncSession, game: str, bundle: Bundle) -> int:
    """Add a bundle to a game's store, with its products.

    Args:
        session: The session that writes the bundle.
        game: The game's data-file name.
        bundle: The bundle.

    Returns:
        The bundle's number.
    """
    number: int | None = await session.scalar(
        insert(StoreBundleRow)
        .values(game=game, **bundle_values(bundle))
        .returning(StoreBundleRow.id)
    )
    assert number is not None
    await add_members(session, number, bundle)
    return number


def bundle_values(bundle: Bundle) -> dict[str, Any]:
    """Return a bundle's fields as its row keeps them, but for its game, members and sales.

    Args:
        bundle: The bundle.
    """
    return {
        "name": bundle.name,
        "description": bundle.description,
        "currency": bundle.currency,
        "price": bundle.price,
        "original_price": bundle.original_price,
        "faction": bundle.faction,
        "hero_class": bundle.hero_class,
        "game_order": bundle.game_order,
        "discount": bundle.discount,
        "starts_at": bundle.starts,
        "ends_at": bundle.ends,
        "box_id": bundle.box,
        "section": bundle.section,
        "pool": bundle.pool,
        "source": bundle.source,
        "texts": {language: list(texts) for language, texts in bundle.texts.items()},
        "stock": bundle.stock,
    }


async def add_members(session: AsyncSession, number: int, bundle: Bundle) -> None:
    """Add a bundle's products to its row.

    Args:
        session: The session that writes the bundle.
        number: The bundle's number.
        bundle: The bundle.
    """
    if bundle.members:
        await session.execute(
            insert(StoreBundleMemberRow),
            [{"bundle_id": number, "product_id": member} for member in bundle.members],
        )


async def add_drops(
    session: AsyncSession,
    box_id: int,
    drops: Sequence[tuple[int, int]],
    weights: Sequence[int],
    rare: Collection[int],
) -> None:
    """Add the drops of a box's table to its row.

    Args:
        session: The session that writes the box.
        box_id: The box's number.
        drops: Each drop's tier and product, in order; a product that two tiers name is dropped
            from the first.
        weights: The weight of each tier, by its number.
        rare: The numbers of the tiers whose drops are rare.
    """
    chosen = {product: tier for tier, product in reversed(drops)}
    if chosen:
        await session.execute(
            insert(StoreDropRow),
            [
                {
                    "box_id": box_id,
                    "product_id": product,
                    "tier": tier,
                    "weight": weights[tier],
                    "rare": tier in rare,
                }
                for product, tier in chosen.items()
            ],
        )


async def _stock_boxes(session: AsyncSession, game: str, boxes: Sequence[NewBox]) -> None:
    stored = await session.execute(
        select(
            StoreProductRow.id,
            StoreProductRow.item_id,
            StoreProductRow.type,
            StoreProductRow.days,
            StoreProductRow.uses,
            StoreProductRow.currency,
        ).where(StoreProductRow.game == game)
    )
    numbers = {
        (item_id, Ownership(type, days, uses), currency): number
        for number, item_id, type, days, uses, currency in stored
    }

    def number(product: NewProduct) -> int:
        return numbers[(product.item_id, product.ownership, product.currency)]

    for box in boxes:
        rule = box.rule
        box_id = await session.scalar(
            insert(StoreBoxRow)
            .values(
                game=game,
                key=rule.key,
                kind=rule.kind,
                currency=rule.currency,
                value=rule.value,
                worth_rule=rule.worth_rule,
                faction=rule.faction,
                hero_class=rule.hero_class,
                fallback_id=number(box.fallback) if box.fallback else None,
            )
            .returning(StoreBoxRow.id)
        )
        assert box_id is not None
        drops = [(tier, number(product)) for tier, product in box.drops]
        weights = [tier.weight for tier in rule.tiers]
        rare = {index for index, tier in enumerate(rule.tiers) if tier.rare}
        await add_drops(session, box_id, drops, weights, rare)


async def _add_boxes(session: AsyncSession, game: str, boxes: Sequence[NewBox]) -> None:
    # The boxes that a stocked store lacks by key, with the products of their drops that it
    # lacks, which only the boxes give.
    held = set(await session.scalars(select(StoreBoxRow.key).where(StoreBoxRow.game == game)))
    new = [box for box in boxes if box.rule.key not in held]
    needed = {product for box in new for _, product in box.drops}
    needed |= {box.fallback for box in new if box.fallback}
    if not needed:
        return
    await session.execute(
        upsert(StoreProductRow).on_conflict_do_nothing(),
        [product_values(game, product) for product in needed],
    )
    await _stock_boxes(session, game, new)


async def _lock(session: AsyncSession, game: str) -> None:
    # Held until the transaction ends, on any server.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"store:{game}"))))


def product_of(row: StoreProductRow) -> Product:
    """Return a product as a row of the store keeps it.

    Args:
        row: The row.
    """
    return Product(
        id=row.id,
        item_id=row.item_id,
        ownership=Ownership(row.type, row.days, row.uses),
        currency=row.currency,
        price=row.price,
        original_price=row.original_price,
        sort=row.sort,
        published=row.published,
    )
