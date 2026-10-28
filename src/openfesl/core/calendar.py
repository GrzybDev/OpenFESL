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


def _zone(name: str) -> str:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        raise ValueError(f"{name!r} is no time zone such as UTC or Europe/Warsaw") from None
    return name


type Zone = Annotated[str, AfterValidator(_zone)]


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
