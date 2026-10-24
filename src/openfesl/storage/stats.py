from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Integer,
    SQLColumnExpression,
    String,
    any_,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.stats import Bucket, Change, Owner, Value
from openfesl.protocol.rank import UpdateType
from openfesl.storage.models import StatRow


class SqlStatStore:
    """Stats in PostgreSQL, one row per owner, key and period."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def values(
        self, partition: str, owners: Sequence[Owner], keys: Sequence[str], bucket: Bucket
    ) -> dict[tuple[Owner, str], Value]:
        """Return the stored stats of owners, leaving out those never stored.

        Args:
            partition: The game's partition.
            owners: Whose stats.
            keys: The stats' keys.
            bucket: The period.
        """
        if not owners or not keys:
            return {}
        # Owners and keys travel as arrays, since a dedicated server's template of keys
        # outnumbers the driver's parameters and a list of row values that long exhausts the
        # database's stack.
        listed = (
            func.unnest(
                literal([owner.type for owner in owners], ARRAY(Integer)),
                literal([owner.id for owner in owners], ARRAY(BigInteger)),
            )
            .table_valued("type", "id")
            .render_derived()
        )
        statement = select(StatRow).where(
            StatRow.partition == partition,
            tuple_(StatRow.owner_type, StatRow.owner_id).in_(select(listed.c.type, listed.c.id)),
            StatRow.period == bucket.period,
            StatRow.period_start == bucket.start,
            StatRow.key == any_(literal(list(keys), ARRAY(String))),
        )
        async with self._sessions() as session:
            rows = await session.scalars(statement)
            return {
                (Owner(row.owner_type, row.owner_id), row.key): Value(row.value, row.text)
                for row in rows
            }

    async def apply(self, partition: str, changes: Sequence[Change]) -> None:
        """Apply updates in their order, all of them or none.

        A value never stored counts as 0, which is the game's own default: games send their
        values as offsets from the defaults in their own data.

        Args:
            partition: The game's partition.
            changes: The updates.
        """
        async with self._sessions.begin() as session:
            for change in changes:
                statement = insert(StatRow).values(
                    partition=partition,
                    owner_type=change.owner.type,
                    owner_id=change.owner.id,
                    period=change.bucket.period,
                    period_start=change.bucket.start,
                    key=change.key,
                    value=_combine(change.kind, literal(0.0), change.value),
                    text=change.text,
                )
                updated: dict[str, Any] = {
                    "value": _combine(change.kind, StatRow.value, change.value)
                }
                if change.text:
                    updated["text"] = change.text
                await session.execute(
                    statement.on_conflict_do_update(constraint="stat_pkey", set_=updated)
                )


def _combine(
    kind: UpdateType, stored: SQLColumnExpression[float], value: float
) -> ColumnElement[float]:
    match kind:
        case UpdateType.SET:
            return literal(value)
        case UpdateType.MAX:
            return func.greatest(stored, value)
        case UpdateType.MIN:
            return func.least(stored, value)
        case UpdateType.ADD:
            return stored + value
        case UpdateType.SUBTRACT:
            return stored - value
