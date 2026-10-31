from datetime import UTC, date, datetime, timedelta

from openfesl.core.calendar import Period, Schedule, Window, next_start, periods
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
    # The next period of each, as the admin panel says when a section changes next.
    assert next_start(weekly, before, "UTC") == MONDAY
    assert next_start(weekly, MONDAY, "UTC") == MONDAY + timedelta(7)
    assert next_start(sale, MONDAY + timedelta(1), "UTC") == MONDAY + timedelta(3)
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


def test_a_run_may_cross_the_year_end_and_never_reaches_the_next_years() -> None:
    winter = Window(dates=("12-20", "01-06"))
    counted = Window(first="12-20", count=18)
    long_before_easter = Window(easter=(-130, 0))

    # Both reach into January, where the run of the year before still runs.
    assert winter.days(2026) == counted.days(2026) == (date(2026, 12, 20), date(2027, 1, 6))
    january = datetime(2027, 1, 3, tzinfo=UTC)
    assert winter.year_at(january, "UTC") == counted.year_at(january, "UTC") == 2026
    assert counted.day(january, "UTC") == 15
    assert winter.running(datetime(2027, 1, 7, tzinfo=UTC), "UTC") is None
    # A run before Easter 2027 already runs in November 2026.
    assert long_before_easter.year_at(datetime(2026, 11, 20, tzinfo=UTC), "UTC") == 2027
    # A year's run may not reach the next one, which starts 365 days or 50 weeks later at the
    # earliest.
    assert not counted.overlaps and not Window(first="03-01", count=365).overlaps
    assert Window(first="03-01", count=366).overlaps
    assert not Window(easter=(-100, 249)).overlaps and Window(easter=(-100, 250)).overlaps
