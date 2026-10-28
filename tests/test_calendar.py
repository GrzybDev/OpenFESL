from datetime import UTC, datetime, timedelta

from openfesl.core.calendar import Period, Schedule, periods

# A Monday at midnight in UTC.
MONDAY = datetime(2026, 11, 2, tzinfo=UTC)


def starts(schedule: Schedule, now: datetime, zone: str = "UTC") -> list[datetime]:
    return [period.start for period in periods(schedule, now, zone)]


def test_periods_start_on_their_days_at_the_zones_midnight_and_last_their_days() -> None:
    weekly, daily = Schedule(every=7), Schedule()
    # Sales from Mondays and Thursdays for three days, so none on Sundays.
    sale = Schedule(every=7, starts=(0, 3), lasts=3)
    # A pair every two weeks for six weeks, so three run at once.
    pairs = Schedule(every=14, lasts=42)
    before = MONDAY - timedelta(microseconds=1)

    assert periods(weekly, before, "UTC") == [Period(MONDAY - timedelta(7), MONDAY)]
    assert periods(weekly, MONDAY, "UTC") == [Period(MONDAY, MONDAY + timedelta(7))]
    assert starts(daily, before) == [MONDAY - timedelta(1)]
    assert starts(sale, MONDAY + timedelta(days=2, hours=23)) == [MONDAY]
    assert starts(sale, MONDAY + timedelta(days=3)) == [MONDAY + timedelta(3)]
    assert starts(sale, MONDAY + timedelta(days=6)) == []
    assert len(starts(pairs, MONDAY)) == 3
    # Midnight in Warsaw is an hour before UTC's in November, and two hours in July.
    warsaw = datetime(2026, 11, 1, 23, tzinfo=UTC)
    assert starts(weekly, warsaw, "Europe/Warsaw") == [warsaw]
    assert starts(daily, datetime(2026, 7, 1, 22, tzinfo=UTC), "Europe/Warsaw") == [
        datetime(2026, 7, 1, 22, tzinfo=UTC)
    ]
