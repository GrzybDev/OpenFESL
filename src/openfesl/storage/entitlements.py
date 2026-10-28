from collections.abc import Sequence

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.entitlements import Entitlement, EntitlementQuery, StatusChange
from openfesl.core.games import Grant
from openfesl.storage.admin import action_row
from openfesl.storage.models import AdminActionRow, EntitlementRow

ACTIVE = "ACTIVE"
# What the game reads for an entitlement the admin took away.
REVOKED = "DISABLED"


class SqlEntitlementStore:
    """Entitlements in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def entitled(self, account_id: int, tag: str, product: str | None = None) -> bool:
        """Tell whether an account holds an active entitlement.

        Args:
            account_id: The account.
            tag: The entitlement's tag.
            product: The product the entitlement must belong to, regardless of case; any when
                omitted.
        """
        held = exists().where(
            EntitlementRow.account_id == account_id,
            EntitlementRow.tag == tag,
            EntitlementRow.status == ACTIVE,
        )
        if product is not None:
            held = held.where(func.upper(EntitlementRow.product_id) == product.upper())
        async with self._sessions() as session:
            return bool(await session.scalar(select(held)))

    async def grant(self, account_id: int, grant: Grant) -> bool:
        """Grant an account an entitlement, which keeps its status when the account holds it.

        Args:
            account_id: The account.
            grant: The entitlement.

        Returns:
            Whether the account holds it active, which it does not once the admin revoked it.
        """
        async with self._sessions.begin() as session:
            await session.execute(grant_statement(account_id, [grant]))
            status = await session.scalar(
                select(EntitlementRow.status).where(
                    EntitlementRow.account_id == account_id, EntitlementRow.tag == grant.tag
                )
            )
            return status == ACTIVE

    async def give(
        self, account_id: int, grants: Sequence[Grant], key: str | None, admin: Admin
    ) -> list[str]:
        """Grant an account entitlements by hand and record it; those it holds keep their status.

        Args:
            account_id: The account.
            grants: The entitlements.
            key: The product key whose grants they are, if any; it is not used up.
            admin: The admin who granted them.

        Returns:
            The tags granted, without those the account held already, revoked ones included.
        """
        if not grants:
            return []
        async with self._sessions.begin() as session:
            tags = [grant.tag for grant in grants]
            held = set(
                await session.scalars(
                    select(EntitlementRow.tag).where(
                        EntitlementRow.account_id == account_id, EntitlementRow.tag.in_(tags)
                    )
                )
            )
            await session.execute(grant_statement(account_id, grants))
            added = [tag for tag in dict.fromkeys(tags) if tag not in held]
            if added:
                details = {"tags": added, "key": key}
                target = str(account_id)
                session.add(action_row(AdminAction.ENTITLEMENT_GRANT, target, details, admin))
            return added

    async def set_status(self, account_id: int, tag: str, active: bool, admin: Admin) -> bool:
        """Revoke an account's entitlement or grant it again, and record it.

        Args:
            account_id: The account.
            tag: The entitlement's tag.
            active: Whether it is granted again; otherwise it is revoked.
            admin: The admin who changed it.

        Returns:
            Whether it changed, which it does not when it was so already.
        """
        status = ACTIVE if active else REVOKED
        async with self._sessions.begin() as session:
            changed = await session.scalar(
                update(EntitlementRow)
                .where(
                    EntitlementRow.account_id == account_id,
                    EntitlementRow.tag == tag,
                    EntitlementRow.status != status,
                )
                .values(status=status)
                .returning(EntitlementRow.id)
            )
            if changed is not None:
                details = {"tag": tag, "status": status}
                target = str(account_id)
                session.add(action_row(AdminAction.ENTITLEMENT_STATE, target, details, admin))
            return changed is not None

    async def changes(self, account_id: int) -> dict[str, StatusChange]:
        """Return when the admin last revoked or granted again each of an account's entitlements.

        Args:
            account_id: The account.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AdminActionRow)
                .where(
                    AdminActionRow.action == AdminAction.ENTITLEMENT_STATE,
                    AdminActionRow.target == str(account_id),
                )
                .order_by(AdminActionRow.id)
            )
            return {row.details["tag"]: StatusChange(row.at, row.admin_name) for row in rows}

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


def grant_statement(account_id: int, grants: Sequence[Grant]) -> Insert:
    """Return the statement that grants an account entitlements; held ones keep their status.

    Args:
        account_id: The account.
        grants: The entitlements, at least one.
    """
    statement = insert(EntitlementRow).values(
        [
            {
                "account_id": account_id,
                "tag": grant.tag,
                "group_name": grant.group,
                "product_id": grant.product,
            }
            for grant in grants
        ]
    )
    # A held entitlement keeps its status, revoked included, but takes the group and product a
    # grant names when it lacks them, since the game's checks compare both.
    return statement.on_conflict_do_update(
        index_elements=[EntitlementRow.account_id, EntitlementRow.tag],
        set_={
            "group_name": func.coalesce(EntitlementRow.group_name, statement.excluded.group_name),
            "product_id": func.coalesce(EntitlementRow.product_id, statement.excluded.product_id),
        },
    )
