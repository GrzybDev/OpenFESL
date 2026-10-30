from dataclasses import dataclass

from openfesl.core.calendar import Period
from openfesl.core.reward_rules import Event


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
