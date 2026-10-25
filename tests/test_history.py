from datetime import UTC, datetime, timedelta

import anyio
import pytest
from support import FESL, enter_as_player, game, host_game, let_in, log_in_persona

from openfesl.core.census import Activity
from openfesl.storage.history import SqlHistoryStore

pytestmark = pytest.mark.anyio


@pytest.mark.usefixtures("quick_counts", "open_hosting")
async def test_the_server_counts_players_online_as_the_peaks_of_hours_and_days(
    server_url: str, history_store: SqlHistoryStore
) -> None:
    async with game(server_url) as server, game(server_url) as playing, game(server_url) as idle:
        created = await host_game(server, {"NAME": "Invented Ridge Rush"})
        await enter_as_player(playing, "ridge@example.invalid", "RidgeRunner")
        await let_in(server, playing, created)
        await log_in_persona(idle, FESL, "idle@example.invalid", "IdleWalker")
        with anyio.fail_after(5):
            while not (days := await history_store.days("bfbc2")) or days[-1].online < 2:
                await anyio.sleep(0.05)
    # The servers' own logins are not players.
    assert (days[-1].online, days[-1].in_matches, days[-1].servers) == (2, 1, 1)
    hours = await history_store.hours("bfbc2", datetime.now(UTC) - timedelta(hours=1))
    assert (hours[-1].online, hours[-1].in_matches) == (2, 1)


async def test_a_peak_keeps_its_first_highest_count_and_old_hours_go_without_their_days(
    history_store: SqlHistoryStore,
) -> None:
    store = history_store
    evening = datetime(2026, 10, 17, 20, tzinfo=UTC)
    for minutes, online, in_matches in (
        (5, 180, 150),
        (55, 214, 186),
        (58, 214, 200),
        (65, 90, 70),
    ):
        await store.record(
            "bfbc2", evening + timedelta(minutes=minutes), Activity(online, in_matches, 10)
        )
    await store.record("bfbc2", evening + timedelta(days=1), Activity(214, 190, 11))

    hours = await store.hours("bfbc2", evening)
    # An equal count later in the hour keeps the time and the matches of the first.
    assert [(peak.start, peak.at, peak.online, peak.in_matches) for peak in hours] == [
        (evening, evening + timedelta(minutes=55), 214, 186),
        (evening + timedelta(hours=1), evening + timedelta(minutes=65), 90, 70),
        (evening + timedelta(days=1), evening + timedelta(days=1), 214, 190),
    ]
    days = await store.days("bfbc2")
    assert [(peak.start.date().isoformat(), peak.online) for peak in days] == [
        ("2026-10-17", 214),
        ("2026-10-18", 214),
    ]
    # The record is the first day that reached the most players.
    best = await store.best("bfbc2")
    assert best is not None
    assert (best.at, best.servers) == (evening + timedelta(minutes=55), 10)

    await store.forget_hours(evening + timedelta(hours=1))
    assert [peak.start for peak in await store.hours("bfbc2", evening)] == [
        evening + timedelta(hours=1),
        evening + timedelta(days=1),
    ]
    assert len(await store.days("bfbc2")) == 2
