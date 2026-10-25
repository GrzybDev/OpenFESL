import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol

from openfesl.core.census import Activity, Census
from openfesl.core.games import Game

logger = logging.getLogger(__name__)

# How often each game's players online are counted, in seconds.
SAMPLE_INTERVAL = 60.0
# How long the peaks of single hours are kept; the peaks of days are kept for good.
HOURS_KEPT = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Peak:
    """The most players that were online at once in an hour or a day, from the counts taken.

    Attributes:
        start: When the hour or day began, in UTC.
        at: When that many were counted; the earliest such count.
        online: The players logged in to the game then.
        in_matches: Those among them in a game that a server hosted.
        servers: The games that servers hosted then.
    """

    start: datetime
    at: datetime
    online: int
    in_matches: int
    servers: int

    @property
    def in_menus(self) -> int:
        """The players logged in then who were in no hosted game."""
        return max(self.online - self.in_matches, 0)


class Period(StrEnum):
    """How far back the activity chart reaches; each period has bars of its own length."""

    DAY = "day"
    """The last 24 hours, a bar for each hour."""
    WEEK = "week"
    """The last 7 days, a bar for each day."""
    MONTH = "month"
    """The last 30 days, a bar for each day."""
    ALL = "all"
    """Every week since the first day counted, a bar for each week from Monday."""

    @property
    def span(self) -> str:
        """How long each of the period's bars is: an hour, a day or a week."""
        return {Period.DAY: "hour", Period.ALL: "week"}.get(self, "day")


@dataclass(frozen=True, slots=True)
class Bar:
    """A bar of the activity chart: an hour, a day or a week, with its peak.

    Attributes:
        start: When its time began, in UTC.
        end: When its time ends.
        peak: The most players online at once then; None when nothing was counted.
        current: Whether its time is still running.
    """

    start: datetime
    end: datetime
    peak: Peak | None
    current: bool


@dataclass(frozen=True, slots=True)
class Chart:
    """The activity chart of a period, with the record of every day counted.

    Attributes:
        period: The period it shows.
        bars: Its bars, oldest first.
        highest: The bar with the most players online, the first of equals; None when nobody
            was online in the period.
        record: The peak of the day with the most players online; None until someone was.
    """

    period: Period
    bars: tuple[Bar, ...]
    highest: Bar | None
    record: Peak | None


@dataclass(frozen=True, slots=True)
class Overview:
    """A game's peak today and its record.

    Attributes:
        today: Today's peak; None until someone was online today.
        record: The peak of the day with the most players online; None until someone was.
    """

    today: Peak | None
    record: Peak | None


class HistoryStore(Protocol):
    """Where the counts of each game's players online are kept, as the peaks of hours and days."""

    async def record(self, game: str, at: datetime, activity: Activity) -> None:
        """Keep a count as the peak of its hour and of its day where it is higher than theirs.

        Args:
            game: The id of the game counted.
            at: When it was counted, in UTC.
            activity: How many played it then.
        """

    async def forget_hours(self, before: datetime) -> None:
        """Drop the peaks of the hours that began before a time.

        Args:
            before: The time.
        """

    async def hours(self, game: str, since: datetime) -> list[Peak]:
        """Return the peaks of a game's hours from one on, oldest first.

        Args:
            game: The id of the game.
            since: The start of the first hour.
        """

    async def days(self, game: str, since: date | None = None) -> list[Peak]:
        """Return the peaks of a game's days, oldest first.

        Args:
            game: The id of the game.
            since: The first day; every day counted when omitted.
        """

    async def best(self, game: str) -> Peak | None:
        """Return the peak of the day with the most players online at once, the first of equals.

        Args:
            game: The id of the game.
        """


class OnlineHistory:
    """Counts each game's players online every minute and keeps the peaks of hours and days."""

    def __init__(self, store: HistoryStore, census: Census, games: Iterable[Game]) -> None:
        """Count the players of some games.

        Args:
            store: Where the peaks are kept.
            census: Counts the players now.
            games: The games counted.
        """
        self._store = store
        self._census = census
        self._games = list(games)

    async def follow(self) -> None:
        """Count the players now and then every SAMPLE_INTERVAL seconds, until cancelled."""
        while True:
            await self.count(datetime.now(UTC))
            await asyncio.sleep(SAMPLE_INTERVAL)

    async def count(self, at: datetime) -> None:
        """Count every game's players and forget the hours kept long enough.

        Args:
            at: The time of the count, in UTC.
        """
        try:
            for game in self._games:
                await self._store.record(game.id, at, self._census.activity(game))
            await self._store.forget_hours(at - HOURS_KEPT)
        except Exception:
            # The database may be away for a moment; the next count tries again.
            logger.exception("Could not record the players online")

    async def overview(self, game: Game, now: datetime) -> Overview:
        """Return a game's peak today and its record.

        Args:
            game: The game.
            now: The time, in UTC, whose day is today.
        """
        today = await self._store.days(game.id, now.date())
        return Overview(_reached(today[0] if today else None), await self._record(game))

    async def chart(self, game: Game, period: Period, now: datetime) -> Chart:
        """Return a game's activity chart of a period that ends now.

        Args:
            game: The game.
            period: How far back the chart reaches.
            now: The time, in UTC, whose hour, day or week is the last bar.
        """
        today = datetime.combine(now.date(), time(), UTC)
        peaks: dict[datetime, Peak] = {}
        if period is Period.DAY:
            hour = now.replace(minute=0, second=0, microsecond=0)
            starts = [hour - timedelta(hours=back) for back in range(23, -1, -1)]
            peaks = {peak.start: peak for peak in await self._store.hours(game.id, starts[0])}
            length = timedelta(hours=1)
        elif period is Period.ALL:
            for peak in await self._store.days(game.id):
                # The first day with the most players in a week is the week's peak.
                week = _monday(peak.start)
                if week not in peaks or peak.online > peaks[week].online:
                    peaks[week] = peak
            first = min(peaks, default=_monday(today))
            weeks = (_monday(today) - first).days // 7 + 1
            starts = [first + timedelta(weeks=index) for index in range(weeks)]
            length = timedelta(weeks=1)
        else:
            days = 7 if period is Period.WEEK else 30
            starts = [today - timedelta(days=back) for back in range(days - 1, -1, -1)]
            peaks = {peak.start: peak for peak in await self._store.days(game.id, starts[0].date())}
            length = timedelta(days=1)
        bars = tuple(
            Bar(start, start + length, peaks.get(start), start == starts[-1]) for start in starts
        )
        counted = [bar for bar in bars if bar.peak and bar.peak.online]
        highest = max(counted, key=lambda bar: bar.peak.online if bar.peak else 0, default=None)
        return Chart(period, bars, highest, await self._record(game))

    async def _record(self, game: Game) -> Peak | None:
        return _reached(await self._store.best(game.id))


def _reached(peak: Peak | None) -> Peak | None:
    # A peak of nobody is no peak.
    return peak if peak and peak.online else None


def _monday(day: datetime) -> datetime:
    return day - timedelta(days=day.weekday())
