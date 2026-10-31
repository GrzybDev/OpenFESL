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
    """

    key: str
    event: Event
    period: Period
    days: int
    day: int | None


def running_at(rules: RewardRules, now: datetime) -> list[Run]:
    """Return the events of a calendar that run at a moment and are switched on.

    Args:
        rules: The rewards with their calendar.
        now: The moment.
    """
    year = today(now, rules.zone).year
    runs = [run_of(key, event, year, rules.zone) for key, event in rules.events.items()]
    return [
        replace(run, day=run.event.window.day(now, rules.zone))
        for run in runs
        if run.event.enabled and run.period.start <= now < run.period.end
    ]


def run_of(key: str, event: Event, year: int, zone: str) -> Run:
    """Return an event of the calendar as it runs in a year, whether it is switched on or not.

    Args:
        key: The event's key.
        event: The event.
        year: The year.
        zone: The time zone whose midnights its days start at.
    """
    first, last = event.window.days(year)
    return Run(key, event, event.window.period(year, zone), (last - first).days + 1, None)
