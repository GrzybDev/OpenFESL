from datetime import UTC, date, datetime, timedelta

from openfesl.core.calendar import Period, Schedule, Window, periods
from openfesl.core.calendar import easter as computed

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


def test_windows_run_on_their_days_of_each_year_and_easter_moves_with_the_computus() -> None:
    easter = Window(easter=(-2, 1))
    halloween = Window(dates=("10-19", "10-31"))

    # Western Easter Sundays, 2038's on the latest day it may fall on.
    assert [computed(year) for year in (2008, 2011, 2024, 2025, 2026, 2038)] == [
        date(2008, 3, 23),
        date(2011, 4, 24),
        date(2024, 3, 31),
        date(2025, 4, 20),
        date(2026, 4, 5),
        date(2038, 4, 25),
    ]
    # Good Friday to Easter Monday.
    assert easter.days(2026) == (date(2026, 4, 3), date(2026, 4, 6))
    assert easter.running(datetime(2026, 4, 6, 23, 59, tzinfo=UTC), "UTC") == Period(
        datetime(2026, 4, 3, tzinfo=UTC), datetime(2026, 4, 7, tzinfo=UTC)
    )
    assert easter.running(datetime(2026, 4, 7, tzinfo=UTC), "UTC") is None
    # The 13 days of Halloween, counted from 1, in a zone whose day starts earlier.
    assert halloween.day(datetime(2026, 10, 19, tzinfo=UTC), "UTC") == 1
    assert halloween.day(datetime(2026, 10, 31, 22, 59, tzinfo=UTC), "UTC") == 13
    assert halloween.day(datetime(2026, 10, 31, 23, tzinfo=UTC), "Europe/Warsaw") is None
    assert halloween.day(datetime(2026, 10, 18, 22, tzinfo=UTC), "Europe/Warsaw") == 1
