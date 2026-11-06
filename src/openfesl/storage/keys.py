from collections.abc import Mapping, Sequence

from sqlalchemy import ColumnElement, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.entitlements import StatusChange
from openfesl.core.games import Grant, ProductKey
from openfesl.core.keys import KeyHistory, KeyLimits, KeyRefusal, StoredKey
from openfesl.storage.admin import action_row
from openfesl.storage.entitlements import grant_statement
from openfesl.storage.models import (
    AdminActionRow,
    ProductKeyGrantRow,
    ProductKeyRow,
    ProductKeyUseRow,
)


class SqlKeyStore:
    """Product keys in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

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
            return [_grant(row) for row in rows]

    async def redeem(self, account_id: int, code: str) -> list[Grant] | KeyRefusal:
        """Grant an account what an enabled product key grants, within the key's limits.

        Entitlements the account holds already keep their status, revoked ones included.

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
            grants = [_grant(row) for row in rows]
            if grants:
                await session.execute(grant_statement(account_id, grants))
            return grants

    async def keys(self) -> list[StoredKey]:
        """Return every product key that grants entitlements, in the order they were added.

        The codes that give gifts on the games' sites are left out.
        """
        async with self._sessions() as session:
            keys = list(
                await session.scalars(
                    select(ProductKeyRow)
                    .where(ProductKeyRow.gift.is_(None))
                    .order_by(ProductKeyRow.id)
                )
            )
            grants: dict[int, list[Grant]] = {key.id: [] for key in keys}
            for row in await session.scalars(select(ProductKeyGrantRow)):
                grants[row.key_id].append(_grant(row))
            counted = await session.execute(
                select(ProductKeyUseRow.key_id, func.count()).group_by(ProductKeyUseRow.key_id)
            )
            uses = {key_id: count for key_id, count in counted}
            return [
                StoredKey(
                    key.code,
                    key.enabled,
                    tuple(grants[key.id]),
                    key.created_at,
                    KeyLimits(key.code, key.max_uses, key.once_per_account, uses.get(key.id, 0)),
                    key.labels,
                    key.game,
                )
                for key in keys
            ]

    async def add_key(self, code: str, game: str, admin: Admin) -> bool:
        """Add an enabled product key that grants nothing yet, and record it.

        Args:
            code: The code entered in the game.
            game: The id of the game whose players it is for.
            admin: The admin who added it.

        Returns:
            Whether it was added, which it is not when a key has the code in any case.
        """
        async with self._sessions.begin() as session:
            added = await session.scalar(
                insert(ProductKeyRow)
                .values(code=code, game=game)
                .on_conflict_do_nothing()
                .returning(ProductKeyRow.id)
            )
            if added is not None:
                session.add(action_row(AdminAction.KEY_ADD, code, {"starting": False}, admin))
            return added is not None

    async def edit_key(
        self,
        code: str,
        grants: Sequence[Grant],
        labels: Mapping[str, str],
        limits: KeyLimits,
        admin: Admin,
    ) -> None:
        """Replace what a product key grants, its labels and its limits, and record the changes.

        Accounts keep what the key granted them before.

        Args:
            code: The key as stored.
            grants: What it grants from now on, each tag once.
            labels: Its public labels by language.
            limits: How often it may be redeemed from now on; its uses are left as they are.
            admin: The admin who changed it.
        """
        async with self._sessions.begin() as session:
            key = await session.scalar(
                select(ProductKeyRow).where(ProductKeyRow.code == code).with_for_update()
            )
            assert key is not None
            await session.execute(
                delete(ProductKeyGrantRow).where(ProductKeyGrantRow.key_id == key.id)
            )
            session.add_all(_grant_rows(key.id, grants))
            details: dict[str, object] = {"tags": [grant.tag for grant in grants]}
            session.add(
                action_row(AdminAction.KEY_GRANTS, code, details | {"labels": labels}, admin)
            )
            if (key.max_uses, key.once_per_account) != (limits.max_uses, limits.once_per_account):
                details = {"max_uses": limits.max_uses, "once_per_account": limits.once_per_account}
                session.add(action_row(AdminAction.KEY_LIMITS, code, details, admin))
            key.labels = dict(labels)
            key.max_uses = limits.max_uses
            key.once_per_account = limits.once_per_account

    async def enable(self, code: str, enabled: bool, admin: Admin) -> bool:
        """Enable or disable a product key and record it.

        Args:
            code: The key as stored.
            enabled: Whether the game accepts it from now on.
            admin: The admin who changed it.

        Returns:
            Whether it changed, which it does not when it was so already.
        """
        async with self._sessions.begin() as session:
            changed = await session.scalar(
                update(ProductKeyRow)
                .where(ProductKeyRow.code == code, ProductKeyRow.enabled != enabled)
                .values(enabled=enabled)
                .returning(ProductKeyRow.id)
            )
            if changed is not None:
                details = {"enabled": enabled}
                session.add(action_row(AdminAction.KEY_STATE, code, details, admin))
            return changed is not None

    async def key_history(self) -> dict[str, KeyHistory]:
        """Return when each product key was added and last enabled or disabled by the admin."""
        actions = [AdminAction.KEY_ADD, AdminAction.KEY_STATE]
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AdminActionRow)
                .where(AdminActionRow.action.in_(actions))
                .order_by(AdminActionRow.id)
            )
            found: dict[str, KeyHistory] = {}
            for row in rows:
                change = StatusChange(row.at, row.admin_name)
                known = found.get(row.target, KeyHistory())
                if row.action == AdminAction.KEY_ADD:
                    found[row.target] = KeyHistory(change, known.changed)
                else:
                    found[row.target] = KeyHistory(known.added, change)
            return found

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
        self, code: str, max_uses: int | None, once_per_account: bool, admin: Admin | None
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
            details = {"max_uses": max_uses, "once_per_account": once_per_account}
            session.add(action_row(AdminAction.KEY_LIMITS, code, details, admin))

    async def has_keys(self) -> bool:
        """Tell whether any product key exists, enabled or not."""
        async with self._sessions() as session:
            return bool(await session.scalar(select(exists().select_from(ProductKeyRow))))

    async def import_keys(self, keys: Sequence[ProductKey], admin: Admin | None = None) -> int:
        """Add the keys whose codes do not exist yet, leaving existing keys untouched.

        Args:
            keys: Keys from the games' data files.
            admin: The admin who added them, whose additions are recorded; none for the server
                and the command line.

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
                if admin is not None:
                    details = {"starting": True}
                    session.add(action_row(AdminAction.KEY_ADD, key.code, details, admin))
                session.add_all(_grant_rows(key_id, key.entitlements))
        return added


def _named(code: str) -> ColumnElement[bool]:
    return func.upper(ProductKeyRow.code) == func.upper(code.strip())


def _grant(row: ProductKeyGrantRow) -> Grant:
    return Grant(tag=row.tag, group=row.group_name, product=row.product_id)


def _grant_rows(key_id: int, grants: Sequence[Grant]) -> list[ProductKeyGrantRow]:
    return [
        ProductKeyGrantRow(
            key_id=key_id, tag=grant.tag, group_name=grant.group, product_id=grant.product
        )
        for grant in grants
    ]


def _enabled(code: str) -> ColumnElement[bool]:
    # A game redeems its keys alone, never the codes that give gifts on its site.
    return _named(code) & ProductKeyRow.enabled & ProductKeyRow.gift.is_(None)


async def _uses(session: AsyncSession, key_id: int) -> int:
    count = select(func.count()).where(ProductKeyUseRow.key_id == key_id)
    return await session.scalar(count) or 0
