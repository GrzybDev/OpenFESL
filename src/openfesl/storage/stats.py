from collections.abc import Collection, Mapping, Sequence
from typing import Any

from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Integer,
    Row,
    SQLColumnExpression,
    String,
    Subquery,
    any_,
    case,
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
from openfesl.core.personas import PersonaState
from openfesl.core.rankings import KD, Line
from openfesl.core.stats import (
    PERSONA,
    Bucket,
    Change,
    LevelPoints,
    Levels,
    Owner,
    Value,
    bucket_of,
)
from openfesl.core.wallets import Wallet
from openfesl.protocol.rank import Period, RankOrder, UpdateType
from openfesl.storage.models import PersonaRow, StatRow
from openfesl.storage.wallets import grant_up_to

# The ledger's reason for the points that a persona earns with its level.
LEVEL = "level"


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

    async def owned(self, partition: str, owner: Owner, bucket: Bucket) -> dict[str, Value]:
        """Return every stat that an owner has stored in a period, by key.

        Args:
            partition: The game's partition.
            owner: Whose stats.
            bucket: The period.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(StatRow).where(
                    StatRow.partition == partition,
                    StatRow.owner_type == owner.type,
                    StatRow.owner_id == owner.id,
                    StatRow.period == bucket.period,
                    StatRow.period_start == bucket.start,
                )
            )
            return {row.key: Value(row.value, row.text) for row in rows}

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

    async def lines(
        self,
        partition: str,
        sums: Mapping[str, Sequence[str]],
        order: str,
        bucket: Bucket,
        among: Collection[int] | None,
        window: slice,
    ) -> list[Line]:
        """Return players' lines in their order, best first.

        Args:
            partition: The game's partition.
            sums: The stats to show by their names, each with the keys whose values it adds up.
            order: The name of the stat the lines are sorted by, highest first, or `KD`.
            bucket: The period.
            among: The personas whose lines to return, keeping everyone's positions; every
                player's when None.
            window: Which of the returned lines, counted from 0.
        """
        placed = _placed(partition, sums, order, bucket)
        statement = (
            select(placed, PersonaRow.name)
            .join(PersonaRow, PersonaRow.id == placed.c.owner_id)
            .order_by(placed.c.place)
            .offset(window.start)
            .limit(window.stop - window.start)
        )
        if among is not None:
            statement = statement.where(placed.c.owner_id == any_(_ids(among)))
        async with self._sessions() as session:
            return [_line(row, sums) for row in await session.execute(statement)]

    async def count(
        self,
        partition: str,
        keys: Sequence[str],
        bucket: Bucket,
        among: Collection[int] | None,
    ) -> int:
        """Return how many players are on the leaderboard.

        Args:
            partition: The game's partition.
            keys: Every key the leaderboard sums up.
            bucket: The period.
            among: Count only these personas; every player when None.
        """
        statement = select(func.count(func.distinct(StatRow.owner_id))).where(
            *_personas(partition, keys, bucket)
        )
        if among is not None:
            statement = statement.where(StatRow.owner_id == any_(_ids(among)))
        async with self._sessions() as session:
            return await session.scalar(statement) or 0

    async def find(
        self,
        partition: str,
        sums: Mapping[str, Sequence[str]],
        order: str,
        bucket: Bucket,
        name: str,
    ) -> Line | None:
        """Return the line of a player found by name: called it, or else holding it, best first.

        Names match regardless of case.

        Args:
            partition: The game's partition.
            sums: The stats to show by their names, each with the keys whose values it adds up.
            order: The name of the stat the lines are sorted by, highest first, or `KD`.
            bucket: The period.
            name: The name or part of it.
        """
        placed = _placed(partition, sums, order, bucket)
        first = (
            select(placed, PersonaRow.name)
            .join(PersonaRow, PersonaRow.id == placed.c.owner_id)
            .order_by(placed.c.place)
            .limit(1)
        )
        async with self._sessions() as session:
            for matching in (
                func.lower(PersonaRow.name) == func.lower(name),
                PersonaRow.name.icontains(name, autoescape=True),
            ):
                if row := (await session.execute(first.where(matching))).first():
                    return _line(row, sums)
        return None

    async def apply(
        self, partition: str, changes: Sequence[Change], levels: Levels | None = None
    ) -> None:
        """Apply updates in their order, all of them or none, with the points a level earns.

        A value never stored counts as 0, which is the game's own default: games send their
        values as offsets from the defaults in their own data.

        Args:
            partition: The game's partition.
            changes: The updates.
            levels: How the personas' levels are kept: each level an update changes stays
                within its bounds, and each persona whose level changes is credited at once the
                points that its level earns and never earned before, so that none is credited
                twice or taken back; none for a game whose personas have no levels.
        """
        async with self._sessions.begin() as session:
            for change in changes:
                value = _combine(change.kind, literal(0.0), change.value)
                combined = _combine(change.kind, StatRow.value, change.value)
                if levels and change.owner.type == PERSONA and change.key == levels.key:
                    value, combined = (_bounded(level, levels) for level in (value, combined))
                statement = insert(StatRow).values(
                    partition=partition,
                    owner_type=change.owner.type,
                    owner_id=change.owner.id,
                    period=change.bucket.period,
                    period_start=change.bucket.start,
                    key=change.key,
                    value=value,
                    text=change.text or "",
                )
                updated: dict[str, Any] = {"value": combined}
                if change.text is not None:
                    updated["text"] = change.text
                await session.execute(
                    statement.on_conflict_do_update(constraint="stat_pkey", set_=updated)
                )
            if levels and levels.points:
                await _grant_points(session, partition, changes, levels.key, levels.points)


async def _grant_points(
    session: AsyncSession,
    partition: str,
    changes: Sequence[Change],
    key: str,
    points: LevelPoints,
) -> None:
    raised = {
        change.owner.id for change in changes if change.owner.type == PERSONA and change.key == key
    }
    if not raised:
        return
    bucket = bucket_of(Period.ALL_TIME, 0)
    levels = await session.execute(
        select(PersonaRow.id, PersonaRow.account_id, StatRow.value)
        .join(StatRow, StatRow.owner_id == PersonaRow.id)
        .where(
            PersonaRow.id.in_(raised),
            StatRow.partition == partition,
            StatRow.owner_type == PERSONA,
            StatRow.key == key,
            StatRow.period == bucket.period,
            StatRow.period_start == bucket.start,
        )
        # One order of the wallets' locks for every transaction.
        .order_by(PersonaRow.id)
    )
    for persona_id, account_id, level in levels:
        earned = int(level) // points.every
        if earned > 0:
            wallet = Wallet(points.game, points.currency, account_id, persona_id, points.allowance)
            await grant_up_to(session, wallet, earned, LEVEL)


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


def _personas(partition: str, keys: Sequence[str], bucket: Bucket) -> list[ColumnElement[bool]]:
    return [
        StatRow.partition == partition,
        StatRow.owner_type == PERSONA,
        StatRow.period == bucket.period,
        StatRow.period_start == bucket.start,
        StatRow.key == any_(literal(list(keys), ARRAY(String))),
        _ranked(StatRow),
    ]


def summed(partition: str, sums: Mapping[str, Sequence[str]], bucket: Bucket) -> Subquery:
    """Return the sums of the stats of each enabled persona that holds any of them in a period.

    Args:
        partition: The game's partition.
        sums: The sums by their names, each with the keys whose values it adds up.
        bucket: The period.
    """
    every = sorted({key for keys in sums.values() for key in keys})
    totals = [
        func.coalesce(
            func.sum(StatRow.value).filter(StatRow.key == any_(literal(list(keys), ARRAY(String)))),
            0.0,
        ).label(name)
        for name, keys in sums.items()
    ]
    return (
        select(StatRow.owner_id, *totals)
        .where(*_personas(partition, every, bucket))
        .group_by(StatRow.owner_id)
        .subquery()
    )


def per(over: ColumnElement[float], under: ColumnElement[float]) -> ColumnElement[float]:
    """Return one sum per another, or the first alone where the other is 0.

    Args:
        over: The sum divided, such as kills.
        under: The sum it is divided by, such as deaths, without which kills count alone.
    """
    return case((under > 0, over / under), else_=over)


def _placed(
    partition: str, sums: Mapping[str, Sequence[str]], order: str, bucket: Bucket
) -> Subquery:
    totals = summed(partition, sums, bucket)
    value = per(totals.c.kills, totals.c.deaths) if order == KD else totals.c[order]
    # Every player has a place of their own, as on the game's own leaderboards.
    place = func.row_number().over(order_by=(value.desc(), totals.c.owner_id))
    return select(totals, place.label("place")).subquery()


def _line(row: Row[Any], sums: Mapping[str, Sequence[str]]) -> Line:
    return Line(row.owner_id, row.name, row.place, {name: row._mapping[name] for name in sums})


def _ids(ids: Collection[int]) -> ColumnElement[list[int]]:
    return literal(list(ids), ARRAY(BigInteger))


def _ranked(row: type[StatRow]) -> ColumnElement[bool]:
    # A persona that its player removed or the admin disabled keeps its stats but leaves the
    # leaderboards, so it takes no one's place.
    in_use = exists().where(
        PersonaRow.id == row.owner_id,
        PersonaRow.disabled_at.is_(None),
        PersonaRow.state != PersonaState.DISABLED,
    )
    return (row.owner_type != PERSONA) | in_use


def _bounded(level: ColumnElement[float], levels: Levels) -> ColumnElement[float]:
    return func.least(func.greatest(level, 1.0), float(levels.highest))


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
