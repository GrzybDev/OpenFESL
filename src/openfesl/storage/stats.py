from collections.abc import Sequence
from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Integer,
    SQLColumnExpression,
    String,
    any_,
    exists,
    false,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from openfesl.core.leaderboards import Ranked, Standing
from openfesl.core.stats import PERSONA, Bucket, Change, Owner, Value
from openfesl.protocol.rank import RankOrder, UpdateType
from openfesl.storage.models import PersonaRow, StatRow


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
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StatRow).where(*_owned(partition, owners, keys, bucket))
            )
            return {
                (Owner(row.owner_type, row.owner_id), row.key): Value(row.value, row.text)
                for row in rows
            }

    async def ranked(
        self,
        partition: str,
        owners: Sequence[Owner],
        keys: Sequence[str],
        order: RankOrder,
        bucket: Bucket,
    ) -> dict[tuple[Owner, str], Ranked]:
        """Return the stored stats of owners with their ranks, leaving out those never stored.

        Args:
            partition: The game's partition.
            owners: Whose stats.
            keys: The stats' keys.
            order: Which values rank first.
            bucket: The period.
        """
        if not owners or not keys:
            return {}
        better = aliased(StatRow)
        ahead = (
            select(func.count())
            .where(
                better.partition == StatRow.partition,
                better.owner_type == StatRow.owner_type,
                better.key == StatRow.key,
                better.period == StatRow.period,
                better.period_start == StatRow.period_start,
                better.value > StatRow.value
                if order == RankOrder.DESCENDING
                else better.value < StatRow.value,
                _ranked(better),
            )
            .correlate(StatRow)
            .scalar_subquery()
        )
        statement = select(StatRow, ahead + 1).where(*_owned(partition, owners, keys, bucket))
        async with self._sessions() as session:
            rows = await session.execute(statement)
            return {
                (Owner(row.owner_type, row.owner_id), row.key): Ranked(row.value, row.text, rank)
                for row, rank in rows
            }

    async def standings(
        self,
        partition: str,
        owner_type: int,
        key: str,
        order: RankOrder,
        bucket: Bucket,
        ranks: range,
    ) -> list[Standing]:
        """Return the owners whose places fall within a window, best first.

        Every owner has a place of its own, so that pages of a leaderboard hold each owner
        once, while owners that tie share their rank. Disabled personas are left out.

        Args:
            partition: The game's partition.
            owner_type: The type of owners ranked.
            key: The ranked stat's key.
            order: Which values rank first.
            bucket: The period.
            ranks: The window of places.
        """
        best = StatRow.value.desc() if order == RankOrder.DESCENDING else StatRow.value.asc()
        ranking = (
            select(
                StatRow.owner_id,
                StatRow.value,
                StatRow.text,
                func.rank().over(order_by=best).label("rank"),
                func.row_number().over(order_by=(best, StatRow.owner_id)).label("place"),
            )
            .where(
                StatRow.partition == partition,
                StatRow.owner_type == owner_type,
                StatRow.key == key,
                StatRow.period == bucket.period,
                StatRow.period_start == bucket.start,
                _ranked(StatRow),
            )
            .subquery()
        )
        named = PersonaRow.id == ranking.c.owner_id if owner_type == PERSONA else false()
        statement = (
            select(ranking, func.coalesce(PersonaRow.name, "").label("name"))
            .outerjoin(PersonaRow, named)
            .where(ranking.c.place.between(ranks.start, ranks.stop - 1))
            .order_by(ranking.c.place)
        )
        async with self._sessions() as session:
            rows = await session.execute(statement)
            return [Standing(row.owner_id, row.name, row.value, row.text, row.rank) for row in rows]

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


def _owned(
    partition: str, owners: Sequence[Owner], keys: Sequence[str], bucket: Bucket
) -> list[ColumnElement[bool]]:
    # Owners and keys travel as arrays, since a dedicated server's template of keys outnumbers
    # the driver's parameters and a list of row values that long exhausts the database's stack.
    listed = (
        func.unnest(
            literal([owner.type for owner in owners], ARRAY(Integer)),
            literal([owner.id for owner in owners], ARRAY(BigInteger)),
        )
        .table_valued("type", "id")
        .render_derived()
    )
    return [
        StatRow.partition == partition,
        tuple_(StatRow.owner_type, StatRow.owner_id).in_(select(listed.c.type, listed.c.id)),
        StatRow.period == bucket.period,
        StatRow.period_start == bucket.start,
        StatRow.key == any_(literal(list(keys), ARRAY(String))),
    ]


def _ranked(row: type[StatRow]) -> ColumnElement[bool]:
    # A disabled persona keeps its stats but leaves the leaderboards, so it takes no one's place.
    in_use = exists().where(PersonaRow.id == row.owner_id, PersonaRow.disabled_at.is_(None))
    return (row.owner_type != PERSONA) | in_use


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
