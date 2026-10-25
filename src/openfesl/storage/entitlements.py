from collections.abc import Sequence

from sqlalchemy import ColumnElement, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.entitlements import Entitlement, EntitlementQuery, KeyLimits, KeyRefusal
from openfesl.core.games import Grant, ProductKey
from openfesl.storage.models import (
    AdminActionRow,
    EntitlementRow,
    ProductKeyGrantRow,
    ProductKeyRow,
    ProductKeyUseRow,
)

ACTIVE = "ACTIVE"


class SqlEntitlementStore:
    """Entitlements and product keys in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def entitled(self, account_id: int, tag: str) -> bool:
        """Tell whether an account holds an active entitlement.

        Args:
            account_id: The account.
            tag: The entitlement's tag.
        """
        async with self._sessions() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            EntitlementRow.account_id == account_id,
                            EntitlementRow.tag == tag,
                            EntitlementRow.status == ACTIVE,
                        )
                    )
                )
            )

    async def grants(self, code: str) -> list[Grant] | None:
        """Return what an enabled product key grants, without granting it.

        Args:
            code: The key, matched regardless of case.

        Returns:
            What the key grants, or None when no enabled key has that code.
        """
        async with self._sessions() as session:
            key = await session.scalar(select(ProductKeyRow).where(_enabled(code)))
            if key is None:
                return None
            rows = await session.scalars(
                select(ProductKeyGrantRow).where(ProductKeyGrantRow.key_id == key.id)
            )
            return [
                Grant(tag=row.tag, group=row.group_name, product=row.product_id) for row in rows
            ]

    async def redeem(self, account_id: int, code: str) -> list[Grant] | KeyRefusal:
        """Grant an account what an enabled product key grants, within the key's limits.

        Entitlements the account holds already, revoked ones included, stay as they are.

        Args:
            account_id: The account.
            code: The key, matched regardless of case.

        Returns:
            What the key grants, or why it grants nothing.
        """
        async with self._sessions.begin() as session:
            # Locked, so that accounts redeeming the key at once count its uses in turn.
            key = await session.scalar(
                select(ProductKeyRow).where(_enabled(code)).with_for_update()
            )
            if key is None:
                return KeyRefusal.UNKNOWN
            used = await session.scalar(
                select(
                    exists().where(
                        ProductKeyUseRow.key_id == key.id, ProductKeyUseRow.account_id == account_id
                    )
                )
            )
            if used and key.once_per_account:
                return KeyRefusal.REDEEMED
            if not used:
                if key.max_uses is not None and await _uses(session, key.id) >= key.max_uses:
                    return KeyRefusal.USED_UP
                session.add(ProductKeyUseRow(key_id=key.id, account_id=account_id))
            rows = list(
                await session.scalars(
                    select(ProductKeyGrantRow).where(ProductKeyGrantRow.key_id == key.id)
                )
            )
            if rows:
                await session.execute(
                    insert(EntitlementRow)
                    .values(
                        [
                            {
                                "account_id": account_id,
                                "tag": row.tag,
                                "group_name": row.group_name,
                                "product_id": row.product_id,
                            }
                            for row in rows
                        ]
                    )
                    .on_conflict_do_nothing()
                )
            return [
                Grant(tag=row.tag, group=row.group_name, product=row.product_id) for row in rows
            ]

    async def grant(self, account_id: int, grant: Grant) -> bool:
        """Grant an account an entitlement, which stays as it is when the account holds it.

        Args:
            account_id: The account.
            grant: The entitlement.

        Returns:
            Whether the account holds it active, which it does not once the admin revoked it.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                insert(EntitlementRow)
                .values(
                    account_id=account_id,
                    tag=grant.tag,
                    group_name=grant.group,
                    product_id=grant.product,
                )
                .on_conflict_do_nothing()
            )
            status = await session.scalar(
                select(EntitlementRow.status).where(
                    EntitlementRow.account_id == account_id, EntitlementRow.tag == grant.tag
                )
            )
            return status == ACTIVE

    async def of_account(self, account_id: int, query: EntitlementQuery) -> list[Entitlement]:
        """Return an account's entitlements, filtered by whichever filters are not empty.

        Args:
            account_id: The account.
            query: The filters.
        """
        statement = select(EntitlementRow).where(EntitlementRow.account_id == account_id)
        if query.id:
            number = int(query.id) if query.id.isdigit() else -1
            statement = statement.where(EntitlementRow.id == number)
        for column, value in [
            (EntitlementRow.tag, query.tag),
            (EntitlementRow.product_id, query.product),
            (EntitlementRow.group_name, query.group),
            (EntitlementRow.status, query.status),
        ]:
            if value:
                statement = statement.where(column == value)
        async with self._sessions() as session:
            rows = await session.scalars(statement.order_by(EntitlementRow.id))
            return [
                Entitlement(
                    id=row.id,
                    tag=row.tag,
                    group=row.group_name,
                    product=row.product_id,
                    status=row.status,
                    granted=row.granted_at,
                )
                for row in rows
            ]

    async def limits(self, code: str) -> KeyLimits | None:
        """Return how often a product key may be redeemed, enabled or not.

        Args:
            code: The key, matched regardless of case.

        Returns:
            Its limits and uses, or None when no key has that code.
        """
        async with self._sessions() as session:
            key = await session.scalar(select(ProductKeyRow).where(_named(code)))
            if key is None:
                return None
            uses = await _uses(session, key.id)
            return KeyLimits(key.code, key.max_uses, key.once_per_account, uses)

    async def limit(
        self, code: str, max_uses: int | None, once_per_account: bool, admin: str | None
    ) -> None:
        """Change how often a product key may be redeemed and record it among the admin's actions.

        Args:
            code: The key as stored.
            max_uses: How many accounts may redeem it; None for any number.
            once_per_account: Whether an account that redeemed it is refused when it enters it
                again.
            admin: The admin who changed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(ProductKeyRow)
                .where(ProductKeyRow.code == code)
                .values(max_uses=max_uses, once_per_account=once_per_account)
            )
            session.add(
                AdminActionRow(
                    admin=admin,
                    action=AdminAction.KEY_LIMITS,
                    target=code,
                    details={"max_uses": max_uses, "once_per_account": once_per_account},
                )
            )

    async def has_keys(self) -> bool:
        """Tell whether any product key exists, enabled or not."""
        async with self._sessions() as session:
            return bool(await session.scalar(select(exists().select_from(ProductKeyRow))))

    async def import_keys(self, keys: Sequence[ProductKey]) -> int:
        """Add the keys whose codes do not exist yet, leaving existing keys untouched.

        Args:
            keys: Keys from the games' data files.

        Returns:
            How many keys were added.
        """
        added = 0
        async with self._sessions.begin() as session:
            for key in keys:
                key_id = await session.scalar(
                    insert(ProductKeyRow)
                    .values(code=key.code)
                    .on_conflict_do_nothing()
                    .returning(ProductKeyRow.id)
                )
                if key_id is None:
                    continue
                added += 1
                session.add_all(
                    ProductKeyGrantRow(
                        key_id=key_id,
                        tag=grant.tag,
                        group_name=grant.group,
                        product_id=grant.product,
                    )
                    for grant in key.entitlements
                )
        return added


def _named(code: str) -> ColumnElement[bool]:
    return func.upper(ProductKeyRow.code) == func.upper(code.strip())


def _enabled(code: str) -> ColumnElement[bool]:
    return _named(code) & ProductKeyRow.enabled


async def _uses(session: AsyncSession, key_id: int) -> int:
    count = select(func.count()).where(ProductKeyUseRow.key_id == key_id)
    return await session.scalar(count) or 0
