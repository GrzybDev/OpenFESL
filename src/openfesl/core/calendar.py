from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)

# The Monday from which schedules count their cycles, so that cycles of whole weeks start on
# Mondays.
_EPOCH = date(2001, 1, 1)


def checked_zone(name: str) -> str:
    """Return the name of a time zone that the zone database knows.

    Args:
        name: The name, such as UTC or Europe/Warsaw.

    Raises:
        ValueError: When the database knows no zone by the name.
    """
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        raise ValueError(f"{name!r} is no time zone such as UTC or Europe/Warsaw") from None
    return name


type Zone = Annotated[str, AfterValidator(checked_zone)]


def _month_day(text: str) -> str:
    # A day of every year, written MM-DD; 29 February is not one.
    try:
        date.fromisoformat(f"2001-{text}")
    except ValueError:
        raise ValueError(f"{text!r} is no day of every year such as 10-31") from None
    return text


type MonthDay = Annotated[str, AfterValidator(_month_day)]


class Schedule(BaseModel):
    """When the periods of something that recurs start and how long each lasts, in whole days.

    Attributes:
        every: The days from one cycle's start to the next.
        starts: The days into each cycle on which a period starts.
        lasts: How many days a period lasts; a cycle's when none, so that one follows the other.
    """

    model_config = ConfigDict(frozen=True)

    every: PositiveInt = 1
    starts: tuple[NonNegativeInt, ...] = (0,)
    lasts: PositiveInt | None = None

    @model_validator(mode="after")
    def _check_starts(self) -> Self:
        if not self.starts or max(self.starts) >= self.every:
            raise ValueError("a schedule's periods start on days within its cycle")
        return self


class Window(BaseModel):
    """The days of each year on which something runs, from its first day to its last.

    Each year has one run, which may end in the next year, as one from 20 December to 6 January
    does, or start in the year before, as one long before Easter does.

    Attributes:
        dates: Its first and last days, written MM-DD; a last day before the first falls in the
            next year.
        easter: Its first and last days as days after Western Easter Sunday, before it below 0.
        first: Its first day, written MM-DD, of a run of a number of days.
        count: How many days that run lasts.
    """

    model_config = ConfigDict(frozen=True)

    dates: tuple[MonthDay, MonthDay] | None = None
    easter: tuple[int, int] | None = None
    first: MonthDay | None = None
    count: PositiveInt | None = None

    @model_validator(mode="after")
    def _check_days(self) -> Self:
        rules = [self.dates, self.easter, self.first]
        if sum(rule is not None for rule in rules) != 1 or (self.first is None) != (
            self.count is None
        ):
            raise ValueError("a window has dates, days after Easter, or a first day and a count")
        if self.easter and self.easter[0] > self.easter[1]:
            raise ValueError("a window's first day is after its last")
        return self

    @property
    def overlaps(self) -> bool:
        """Whether a year's run would reach the next year's, which the runs of events must not."""
        if self.count:
            return self.count > 365
        if self.easter:
            # Two Easter Sundays are at least 50 weeks apart.
            return self.easter[1] - self.easter[0] >= 350
        return False

    def days(self, year: int) -> tuple[date, date]:
        """Return the first and last days of the window's run of a year.

        Args:
            year: The year, which the run's Easter or its first day belongs to.
        """
        if self.easter:
            sunday = easter(year)
            return sunday + timedelta(self.easter[0]), sunday + timedelta(self.easter[1])
        if self.first and self.count:
            first = date.fromisoformat(f"{year}-{self.first}")
            return first, first + timedelta(self.count - 1)
        assert self.dates
        first, last = (date.fromisoformat(f"{year}-{day}") for day in self.dates)
        return first, last if last >= first else last.replace(year=year + 1)

    def period(self, year: int, zone: str) -> Period:
        """Return the window's run of a year, from its first day's start to its last's end.

        Args:
            year: The year.
            zone: The time zone whose midnights its days start at.
        """
        first, last = self.days(year)
        return Period(day_start(first, zone), day_start(last + timedelta(1), zone))

    def year_at(self, now: datetime, zone: str) -> int | None:
        """Return the year of the window's run that runs at a moment, if one does.

        Args:
            now: The moment.
            zone: The time zone whose midnights its days start at.
        """
        year = today(now, zone).year
        for found in (year - 1, year, year + 1):
            period = self.period(found, zone)
            if period.start <= now < period.end:
                return found
        return None

    def running(self, now: datetime, zone: str) -> Period | None:
        """Return the window's run at a moment, if one runs.

        Args:
            now: The moment.
            zone: The time zone whose midnights its days start at.
        """
        year = self.year_at(now, zone)
        return None if year is None else self.period(year, zone)

    def day(self, now: datetime, zone: str) -> int | None:
        """Return which of the window's days a moment falls on, from 1, if it falls on one.

        Args:
            now: The moment.
            zone: The time zone whose midnights its days start at.
        """
        year = self.year_at(now, zone)
        if year is None:
            return None
        return (today(now, zone) - self.days(year)[0]).days + 1


@dataclass(frozen=True, slots=True)
class Period:
    """A stretch of time that something runs for.

    Attributes:
        start: When it starts.
        end: When it ends, which is no longer part of it.
    """

    start: datetime
    end: datetime


def day_start(day: date, zone: str) -> datetime:
    """Return when a day starts in a time zone, in UTC.

    Args:
        day: The day.
        zone: The zone's name, such as UTC.
    """
    return datetime.combine(day, time(), ZoneInfo(zone)).astimezone(UTC)


def today(now: datetime, zone: str) -> date:
    """Return the day a moment falls on in a time zone.

    Args:
        now: The moment.
        zone: The zone's name.
    """
    return now.astimezone(ZoneInfo(zone)).date()


def periods(schedule: Schedule, now: datetime, zone: str) -> list[Period]:
    """Return the periods of a schedule that run at a moment, the latest first.

    Args:
        schedule: The schedule.
        now: The moment.
        zone: The time zone whose midnights the periods start and end at.
    """
    lasts = schedule.lasts or schedule.every
    number = (today(now, zone) - _EPOCH).days
    found = []
    for day in range(number, number - lasts, -1):
        if day % schedule.every in schedule.starts:
            first = _EPOCH + timedelta(days=day)
            period = Period(day_start(first, zone), day_start(first + timedelta(lasts), zone))
            if period.start <= now < period.end:
                found.append(period)
    return found


def next_start(schedule: Schedule, now: datetime, zone: str) -> datetime:
    """Return when the next period of a schedule starts after a moment.

    Args:
        schedule: The schedule.
        now: The moment.
        zone: The time zone whose midnights the periods start at.
    """
    number = (today(now, zone) - _EPOCH).days + 1
    while number % schedule.every not in schedule.starts:
        number += 1
    return day_start(_EPOCH + timedelta(days=number), zone)


def easter(year: int) -> date:
    """Return the Western Easter Sunday of a year, by the Gregorian computus.

    Args:
        year: The year.
    """
    # The anonymous Gregorian algorithm (Meeus, Jones and Butcher).
    golden = year % 19
    century, rest = divmod(year, 100)
    leaps, leap = divmod(century, 4)
    proemptosis = (century - (century + 8) // 25 + 1) // 3
    epact = (19 * golden + century - leaps - proemptosis + 15) % 30
    quarter, remainder = divmod(rest, 4)
    weekday = (32 + 2 * leap + 2 * quarter - epact - remainder) % 7
    shift = (golden + 11 * epact + 22 * weekday) // 451
    month, day = divmod(epact + weekday - 7 * shift + 114, 31)
    return date(year, month, day + 1)
