from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.entitlements import ACTIVE, Entitlement, EntitlementQuery, StatusChange
from openfesl.core.games import Grant
from openfesl.core.inventory import COUNTED, DELETED, RENTED, Held, Ownership, fold
from openfesl.storage.admin import action_row
from openfesl.storage.models import AdminActionRow, EntitlementRow

# What the game reads for an entitlement the admin took away.
REVOKED = "DISABLED"

# What an account owns itself, apart from what its personas own each.
_ACCOUNTS = EntitlementRow.persona_id.is_(None)
# A persona's item that the admin has not taken away, which its game may change.
_IN_USE = (EntitlementRow.persona_id.is_not(None), EntitlementRow.status == ACTIVE)
# A persona's item is named before the first '-' of its tag, how it is owned after.
_item_number = func.split_part(EntitlementRow.tag, "-", 1)


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
            _ACCOUNTS,
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
                    _ACCOUNTS,
                    EntitlementRow.account_id == account_id,
                    EntitlementRow.tag == grant.tag,
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
                        _ACCOUNTS,
                        EntitlementRow.account_id == account_id,
                        EntitlementRow.tag.in_(tags),
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
                    _ACCOUNTS,
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
        statement = select(EntitlementRow).where(_ACCOUNTS, EntitlementRow.account_id == account_id)
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
            return [_entitlement(row) for row in rows]

    async def of_persona(self, persona_id: int, tag: str = "") -> list[Entitlement]:
        """Return the items a persona owns, one entitlement per item.

        Args:
            persona_id: The persona.
            tag: A tag whose item alone is returned, however the tag says it is owned; every
                item when empty.
        """
        statement = select(EntitlementRow).where(EntitlementRow.persona_id == persona_id)
        if tag:
            statement = statement.where(_item_number == tag.partition("-")[0])
        async with self._sessions() as session:
            rows = await session.scalars(statement.order_by(EntitlementRow.id))
            return [_entitlement(row) for row in rows]

    async def holder(self, entitlement_id: int) -> int | None:
        """Return the persona whose item in use an entitlement is, if it is one.

        Args:
            entitlement_id: The entitlement.
        """
        async with self._sessions() as session:
            return await session.scalar(
                select(EntitlementRow.persona_id).where(
                    EntitlementRow.id == entitlement_id, *_IN_USE
                )
            )

    async def use_up(self, entitlement_id: int, uses: int) -> None:
        """Take uses from a persona's item in use, leaving it none rather than fewer.

        Args:
            entitlement_id: The item's entitlement.
            uses: How many uses were used.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(EntitlementRow)
                .where(EntitlementRow.id == entitlement_id, *_IN_USE)
                .values(
                    use_count=func.greatest(func.coalesce(EntitlementRow.use_count, 0) - uses, 0)
                )
            )

    async def stack_uses(self, account_id: int, persona_id: int, tag: str, uses: int) -> None:
        """Add uses to a persona's row of a tag that names no item, creating the row if missing.

        The row is added or changed in one statement, so stacks at once add up.

        Args:
            account_id: The persona's account.
            persona_id: The persona.
            tag: The tag, such as that of the persona's stack of treasure chests.
            uses: How many uses to add.
        """
        statement = insert(EntitlementRow).values(
            account_id=account_id, persona_id=persona_id, tag=tag, use_count=uses
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[EntitlementRow.persona_id, _item_number],
                    index_where=EntitlementRow.persona_id.is_not(None),
                    set_={"use_count": func.coalesce(EntitlementRow.use_count, 0) + uses},
                )
            )

    async def take_out(self, entitlement_id: int, status: str) -> None:
        """Take a persona's item out of use, or out of its list, as its game says.

        Args:
            entitlement_id: The item's entitlement, which is in use.
            status: DISABLED, which ends a rental or an item limited by uses and keeps any
                other listed out of use, or DELETED, which removes it.
        """
        async with self._sessions.begin() as session:
            row = await session.scalar(
                select(EntitlementRow)
                .where(EntitlementRow.id == entitlement_id, *_IN_USE)
                .with_for_update()
            )
            if row is None:
                return
            ownership = _held(row).type
            # The game disables the items that ran out (inferred), so a rental or uses end as
            # they do by themselves, and the hero may rent or buy the item again.
            if status == DELETED:
                await session.delete(row)
            elif ownership == RENTED:
                now = datetime.now(UTC)
                row.expires_at = min(row.expires_at or now, now)
            elif ownership == COUNTED:
                row.use_count = 0
            else:
                row.status = status


async def add_item(
    session: AsyncSession, account_id: int, persona_id: int, item_id: int, gained: Ownership
) -> Held | None:
    """Fold an item that a persona gains into its one row of the item, under the row's lock.

    Args:
        session: The transaction to add it in.
        account_id: The persona's account.
        persona_id: The persona.
        item_id: The item's number.
        gained: How the persona gains it.

    Returns:
        How the persona holds the item then, or None when that changes nothing: it owns the item
        for good, or the item is out of use, as when the admin took it away.
    """
    now = datetime.now(UTC)
    row = await _locked_item(session, persona_id, item_id)
    if row is None:
        held = fold(None, gained, now)
        assert held is not None
        added = await session.scalar(
            insert(EntitlementRow)
            .values(
                account_id=account_id,
                persona_id=persona_id,
                tag=f"{item_id}-{held.type}",
                use_count=held.uses,
                expires_at=held.expires,
            )
            .on_conflict_do_nothing()
            .returning(EntitlementRow.id)
        )
        if added is not None:
            return held
        # Another transaction added the item meanwhile; its row is folded into.
        row = await _locked_item(session, persona_id, item_id)
        assert row is not None
    folded = fold(_held(row), gained, now) if row.status == ACTIVE else None
    if folded is not None:
        row.tag = f"{item_id}-{folded.type}"
        row.use_count = folded.uses
        row.expires_at = folded.expires
    return folded


async def add_account_item(session: AsyncSession, account_id: int, tag: str, uses: int) -> int:
    """Add uses of an item that an account owns itself, such as keys, creating its one row.

    Args:
        session: The transaction to add them in.
        account_id: The account.
        tag: The item's tag.
        uses: How many uses to add.

    Returns:
        The uses the account holds then.
    """
    statement = insert(EntitlementRow).values(account_id=account_id, tag=tag, use_count=uses)
    held = await session.scalar(
        statement.on_conflict_do_update(
            index_elements=[EntitlementRow.account_id, EntitlementRow.tag],
            index_where=_ACCOUNTS,
            set_={"use_count": func.coalesce(EntitlementRow.use_count, 0) + uses},
        ).returning(EntitlementRow.use_count)
    )
    return int(held or 0)


async def use_one(session: AsyncSession, account_id: int, persona_id: int | None, tag: str) -> bool:
    """Take one use of an item in use, of an account or of one of its personas, if it has one.

    The check and the change are one statement, so two at once never take the last use twice.

    Args:
        session: The transaction to take it in.
        account_id: The account.
        persona_id: The persona whose item it is; none for the account's own.
        tag: The item's tag.

    Returns:
        Whether a use was taken.
    """
    # By the columns of the unique index of a persona's items, or of the account's own, which
    # then finds the row instead of a scan of every entitlement.
    owner = (
        (EntitlementRow.persona_id == persona_id, _item_number == tag.partition("-")[0])
        if persona_id is not None
        else (_ACCOUNTS,)
    )
    taken = await session.scalar(
        update(EntitlementRow)
        .where(
            EntitlementRow.account_id == account_id,
            *owner,
            EntitlementRow.tag == tag,
            EntitlementRow.status == ACTIVE,
            EntitlementRow.use_count >= 1,
        )
        .values(use_count=EntitlementRow.use_count - 1)
        .returning(EntitlementRow.id)
    )
    return taken is not None


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
        index_where=_ACCOUNTS,
        set_={
            "group_name": func.coalesce(EntitlementRow.group_name, statement.excluded.group_name),
            "product_id": func.coalesce(EntitlementRow.product_id, statement.excluded.product_id),
        },
    )


def _entitlement(row: EntitlementRow) -> Entitlement:
    return Entitlement(
        id=row.id,
        tag=row.tag,
        group=row.group_name,
        product=row.product_id,
        status=row.status,
        granted=row.granted_at,
        uses=row.use_count,
        expires=row.expires_at,
    )


async def _locked_item(
    session: AsyncSession, persona_id: int, item_id: int
) -> EntitlementRow | None:
    return await session.scalar(
        select(EntitlementRow)
        .where(EntitlementRow.persona_id == persona_id, _item_number == str(item_id))
        .with_for_update()
    )


def _held(row: EntitlementRow) -> Held:
    return Held(row.tag.split("-")[1] if "-" in row.tag else "", row.expires_at, row.use_count)
