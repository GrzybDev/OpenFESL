from collections.abc import Sequence

from sqlalchemy import Select, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.entitlements import Entitlement, EntitlementQuery
from openfesl.core.games import Grant, ProductKey
from openfesl.storage.models import EntitlementRow, ProductKeyGrantRow, ProductKeyRow

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
            key = await session.scalar(_enabled_key(code))
            if key is None:
                return None
            rows = await session.scalars(
                select(ProductKeyGrantRow).where(ProductKeyGrantRow.key_id == key.id)
            )
            return [
                Grant(tag=row.tag, group=row.group_name, product=row.product_id) for row in rows
            ]

    async def redeem(self, account_id: int, code: str) -> list[Grant] | None:
        """Grant an account what an enabled product key grants.

        Entitlements the account holds already, revoked ones included, stay as they are.

        Args:
            account_id: The account.
            code: The key, matched regardless of case.

        Returns:
            What the key grants, or None when no enabled key has that code.
        """
        async with self._sessions.begin() as session:
            key = await session.scalar(_enabled_key(code))
            if key is None:
                return None
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
                    granted=row.granted_at.date(),
                )
                for row in rows
            ]

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


def _enabled_key(code: str) -> Select[ProductKeyRow]:
    return select(ProductKeyRow).where(
        func.upper(ProductKeyRow.code) == func.upper(code.strip()), ProductKeyRow.enabled
    )
