from dataclasses import dataclass, replace
from datetime import datetime

from openfesl.core.calendar import Period, today
from openfesl.core.reward_rules import Event, RewardRules


@dataclass(frozen=True, slots=True)
class Run:
    """An event of the calendar in the year it runs or runs next.

    Attributes:
        key: The event's key.
        event: The event.
        period: When it runs.
        days: How many days it runs.
        day: Which of its days today is, from 1; none when it does not run today.
        year: The year of the run, which its Easter or its first day belongs to and which
            names its claims.
    """

    key: str
    event: Event
    period: Period
    days: int
    day: int | None
    year: int


def running_at(rules: RewardRules, now: datetime) -> list[Run]:
    """Return the events of a calendar that run at a moment and are switched on.

    Args:
        rules: The rewards with their calendar.
        now: The moment.
    """
    found = []
    for key, event in rules.events.items():
        year = event.window.year_at(now, rules.zone)
        if event.enabled and year is not None:
            run = run_of(key, event, year, rules.zone)
            found.append(replace(run, day=event.window.day(now, rules.zone)))
    return found


def run_of(key: str, event: Event, year: int, zone: str) -> Run:
    """Return an event of the calendar as it runs in a year, whether it is switched on or not.

    Args:
        key: The event's key.
        event: The event.
        year: The year, which the run's Easter or its first day belongs to.
        zone: The time zone whose midnights its days start at.
    """
    first, last = event.window.days(year)
    return Run(key, event, event.window.period(year, zone), (last - first).days + 1, None, year)


def run_now(key: str, event: Event, now: datetime, zone: str) -> Run:
    """Return the run of an event that runs at a moment, else its run of the moment's year.

    Args:
        key: The event's key.
        event: The event.
        now: The moment.
        zone: The time zone whose midnights its days start at.
    """
    return run_of(key, event, event.window.year_at(now, zone) or today(now, zone).year, zone)
