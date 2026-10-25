from datetime import UTC, date, datetime, time

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.census import Activity
from openfesl.core.history import Peak
from openfesl.storage.models import ActivityDayRow, ActivityHourRow


class SqlHistoryStore:
    """The peaks of each game's players online in PostgreSQL, by hour and by day."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def record(self, game: str, at: datetime, activity: Activity) -> None:
        """Keep a count as the peak of its hour and of its day where it is higher than theirs.

        Args:
            game: The id of the game counted.
            at: When it was counted, in UTC.
            activity: How many played it then.
        """
        counted = {
            "game": game,
            "at": at,
            "online": activity.online,
            "in_matches": activity.in_matches,
            "servers": activity.servers,
        }
        hour = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        async with self._sessions.begin() as session:
            await _keep_higher(session, ActivityHourRow, "hour", {"hour": hour, **counted})
            await _keep_higher(session, ActivityDayRow, "day", {"day": hour.date(), **counted})

    async def forget_hours(self, before: datetime) -> None:
        """Drop the peaks of the hours that began before a time.

        Args:
            before: The time.
        """
        async with self._sessions.begin() as session:
            await session.execute(delete(ActivityHourRow).where(ActivityHourRow.hour < before))

    async def hours(self, game: str, since: datetime) -> list[Peak]:
        """Return the peaks of a game's hours from one on, oldest first.

        Args:
            game: The id of the game.
            since: The start of the first hour.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ActivityHourRow)
                .where(ActivityHourRow.game == game, ActivityHourRow.hour >= since)
                .order_by(ActivityHourRow.hour)
            )
            return [Peak(row.hour, row.at, row.online, row.in_matches, row.servers) for row in rows]

    async def days(self, game: str, since: date | None = None) -> list[Peak]:
        """Return the peaks of a game's days, oldest first.

        Args:
            game: The id of the game.
            since: The first day; every day counted when omitted.
        """
        query = select(ActivityDayRow).where(ActivityDayRow.game == game)
        if since is not None:
            query = query.where(ActivityDayRow.day >= since)
        async with self._sessions() as session:
            rows = await session.scalars(query.order_by(ActivityDayRow.day))
            return [_day(row) for row in rows]

    async def best(self, game: str) -> Peak | None:
        """Return the peak of the day with the most players online at once, the first of equals.

        Args:
            game: The id of the game.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(ActivityDayRow)
                .where(ActivityDayRow.game == game)
                .order_by(ActivityDayRow.online.desc(), ActivityDayRow.day)
                .limit(1)
            )
            return _day(row) if row else None


async def _keep_higher(
    session: AsyncSession,
    table: type[ActivityHourRow | ActivityDayRow],
    span: str,
    values: dict[str, object],
) -> None:
    statement = insert(table).values(values)
    # An equal count keeps the earlier one, so a peak's time is when it was first reached.
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=["game", span],
            set_={
                name: statement.excluded[name] for name in ("at", "online", "in_matches", "servers")
            },
            where=table.online < statement.excluded.online,
        )
    )


def _day(row: ActivityDayRow) -> Peak:
    start = datetime.combine(row.day, time(), UTC)
    return Peak(start, row.at, row.online, row.in_matches, row.servers)
