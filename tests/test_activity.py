import re
from datetime import UTC, datetime, time, timedelta
from html import unescape
from html.parser import HTMLParser

import anyio
import pytest
from support import FESL, fetch, game, log_in_persona

from openfesl.core.census import Activity
from openfesl.storage.history import SqlHistoryStore

pytestmark = pytest.mark.anyio


class Text(HTMLParser):
    """A page's text, one space between the texts of its elements."""

    def __init__(self, body: bytes) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.feed(body.decode())

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(" ".join(data.split()))

    def __str__(self) -> str:
        return " ".join(self.parts)


def day(value: datetime) -> str:
    """Return a day as the English pages write it, such as Sat 17 Oct."""
    return f"{value:%a} {value.day} {value:%b}"


async def test_a_new_server_shows_no_history_and_no_peak_on_the_front_page(
    server_url: str,
) -> None:
    status, _, body = await fetch(server_url, "/bfbc2/activity")
    page = str(Text(body))
    assert status == 200
    for shown in ("Today's peak None yet Record None yet", "No history yet", "No days recorded"):
        assert shown in page
    _, _, body = await fetch(server_url, "/bfbc2")
    assert "Today's peak" not in str(Text(body))


@pytest.mark.usefixtures("quick_counts")
async def test_the_activity_page_shows_the_peaks_by_hour_day_and_week_and_the_record(
    server_url: str, history_store: SqlHistoryStore
) -> None:
    today = datetime.combine(datetime.now(UTC).date(), time(), UTC)
    busiest = today - timedelta(days=3)
    await history_store.record(
        "bfbc2", busiest + timedelta(hours=20, minutes=55), Activity(214, 186, 10)
    )
    async with game(server_url) as first, game(server_url) as second:
        await log_in_persona(first, FESL, "kestrel@example.invalid", "KestrelSix")
        await log_in_persona(second, FESL, "fox@example.invalid", "NordicFox")
        with anyio.fail_after(5):
            while (
                not (days := await history_store.days("bfbc2", today.date())) or days[0].online < 2
            ):
                await anyio.sleep(0.05)

        status, _, body = await fetch(server_url, "/bfbc2/activity")
        page = str(Text(body))
        assert status == 200
        # Today's peak with the time it was reached, and the busiest day as the record.
        assert re.search(r"Today's peak 2 At \d\d:\d\d UTC", page)
        assert f"Record 214 {day(busiest)}, 20:55 UTC" in page
        assert "Most online per hour · UTC Highest 2 · " in page
        assert f"Record 214 · {busiest.day} {busiest:%b}" in page
        # The values table lists all 24 hours, the last one still counting.
        assert len(re.findall(r"\d\d:00\u2013\d\d:00", page)) == 23
        assert re.search(r"\d\d:00, so far 2 0 2", page)
        # The daily peaks list the last seven days, today's still counting, then the record.
        assert re.search(rf"{day(today)} So far 2 \d\d:\d\d 0 0 ", page)
        assert f"{day(busiest)} 214 20:55 186 10" in page
        assert f"Record {day(busiest)} 214 20:55 186 10" in page

        _, _, body = await fetch(server_url, "/bfbc2/activity?period=week")
        assert f"Most online per day · UTC Highest 214 · {day(busiest)}" in str(Text(body))
        # The chart tells screen readers what it shows.
        assert f"Highest: 214 on {day(busiest)}, the record." in unescape(body.decode())

        _, _, body = await fetch(server_url, "/bfbc2/activity?period=all")
        page = str(Text(body))
        monday = busiest - timedelta(days=busiest.weekday())
        assert f"Since {monday.day} {monday:%b} {monday.year}" in page
        assert f"Most online per week · UTC Highest 214 · week of {monday.day} {monday:%b}" in page

        # An unknown period shows the last 24 hours.
        _, _, body = await fetch(server_url, "/bfbc2/activity?period=decade")
        assert "Last 24 hours" in str(Text(body))

        _, _, body = await fetch(server_url, "/pl/bfbc2/activity?period=month")
        page = str(Text(body))
        for shown in ("Ostatnie 30 dni", "Dziś najwięcej 2", "Rekord 214", "Dzienne szczyty"):
            assert shown in page

        # The front page names today's peak and the record, and leads to the activity page.
        _, _, body = await fetch(server_url, "/bfbc2")
        assert re.search(
            rf"Today's peak so far: 2 at \d\d:\d\d UTC · record: 214 on {day(busiest)}",
            str(Text(body)),
        )
        assert re.search(rb'<a href="/bfbc2/activity"\s+class="peak-line', body)
        _, _, body = await fetch(server_url, "/pl/bfbc2")
        assert re.search(r"Dziś najwięcej: 2 o \d\d:\d\d UTC · rekord: 214, ", str(Text(body)))
