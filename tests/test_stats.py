from datetime import date, datetime, time, timedelta

import anyio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import CREATE_GAME, Game, enter_theater, game, host_game, log_in_player, log_in_server

from openfesl.core.stats import GAME_WIDE, MAXIMUM_ENTRIES, Bucket, Owner, Value, period_start
from openfesl.protocol.fields import Fields
from openfesl.protocol.rank import Period
from openfesl.protocol.relay import Channel, ConnectionKey
from openfesl.server.cli import main
from openfesl.storage.stats import SqlStatStore

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)

type Update = tuple[int, str, str, str, str]


async def play_as(player: Game, nuid: str, name: str) -> str:
    await log_in_player(player, FESL, nuid)
    await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", name)])
    playing = await player.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", name)])
    return playing["userId"]


async def host(server: Game) -> None:
    # Dedicated servers write stats only while they host a game players see.
    await anyio.to_thread.run_sync(main, ["hosting", "open"])
    await host_game(server)


def keys(*names: str) -> Fields:
    return [
        *((f"keys.{index}", name) for index, name in enumerate(names)),
        ("keys.[]", str(len(names))),
    ]


def get_stats(
    owner: str, *names: str, owner_type: str = "1", period: str = "0", past: str = "0"
) -> Fields:
    # As the SDK writes it: the owner and its type only when the caller names an owner.
    owned = [("owner", owner), ("ownerType", owner_type)] if owner else []
    return [("TXN", "GetStats"), *owned, ("periodId", period), ("periodPast", past), *keys(*names)]


def update_stats(*owners: tuple[str, str, list[Update]]) -> Fields:
    # Each owner as owner id, owner type and updates; no id stands for the session's persona.
    fields: Fields = [("TXN", "UpdateStats")]
    for number, (owner, owner_type, updates) in enumerate(owners):
        if owner:
            fields += [(f"u.{number}.o", owner), (f"u.{number}.ot", owner_type)]
        for index, (kind, key, value, text, periods) in enumerate(updates):
            prefix = f"u.{number}.s.{index}."
            fields += [
                (prefix + "ut", str(kind)),
                (prefix + "k", key),
                (prefix + "v", value),
                (prefix + "t", text),
                (prefix + "pt", periods),
            ]
        fields.append((f"u.{number}.s.[]", str(len(updates))))
    return [*fields, ("u.[]", str(len(owners)))]


def values(answer: dict[str, str], prefix: str = "stats.") -> list[tuple[str, str]]:
    count = int(answer[prefix + "[]"])
    return [
        (answer[f"{prefix}{index}.key"], answer[f"{prefix}{index}.value"]) for index in range(count)
    ]


async def test_a_dedicated_server_keeps_a_players_stats_between_rounds(server_url: str) -> None:
    async with game(server_url) as player, game(server_url) as server:
        ann = await play_as(player, "ann@example.test", "Ann_01")
        await host(server)
        template = ("score", "kills", "best_streak", "fastest_lap", "elo")

        joined = await server.request(FESL, "rank", get_stats(ann, *template))
        assert values(joined) == [(key, "0.0000") for key in template]
        assert "stats.4.text" not in joined

        first_round = [
            (3, "score", "100.0000", "", "0"),
            (3, "kills", "5.0000", "", "0"),
            (1, "best_streak", "7.0000", "", "0"),
            (2, "fastest_lap", "-3.0000", "", "0"),
            (0, "elo", "12.5000", "20261024", "0"),
        ]
        assert "errorCode" not in await server.request(
            FESL, "rank", update_stats((ann, "1", first_round))
        )
        second_round = [
            (3, "score", "50.2500", "", "0"),
            (4, "kills", "2.0000", "", "0"),
            (1, "best_streak", "4.0000", "", "0"),
            (2, "fastest_lap", "-1.0000", "", "0"),
            (0, "elo", "3.0000", "", "0"),
        ]
        await server.request(FESL, "rank", update_stats((ann, "1", second_round)))

        rejoined = await server.request(FESL, "rank", get_stats(ann, *template))
        assert values(rejoined) == [
            ("score", "150.2500"),
            ("kills", "3.0000"),
            ("best_streak", "7.0000"),
            ("fastest_lap", "-3.0000"),
            ("elo", "3.0000"),
        ]
        assert rejoined["stats.4.text"] == "20261024"
        own = await player.request(FESL, "rank", get_stats("", "kills", "score"))
        assert values(own) == [("kills", "3.0000"), ("score", "150.2500")]

        both = await player.request(
            FESL,
            "rank",
            [
                ("TXN", "GetStatsForOwners"),
                ("owners.0.ownerId", ann),
                ("owners.0.ownerType", "1"),
                ("owners.1.ownerId", "999999"),
                ("owners.1.ownerType", "1"),
                ("owners.[]", "2"),
                ("periodId", "0"),
                *keys("score", "kills"),
            ],
        )
        assert (both["stats.[]"], both["stats.0.ownerId"], both["stats.1.ownerId"]) == (
            "2",
            ann,
            "999999",
        )
        assert values(both, "stats.0.stats.") == [("score", "150.2500"), ("kills", "3.0000")]
        assert values(both, "stats.1.stats.") == [("score", "0.0000"), ("kills", "0.0000")]


async def test_stats_are_kept_per_period_and_for_the_whole_game(server_url: str) -> None:
    async with game(server_url) as player, game(server_url) as server:
        ann = await play_as(player, "ann@example.test", "Ann_01")
        await host(server)
        await server.request(
            FESL, "rank", update_stats((ann, "1", [(3, "score", "10.0000", "", "0,2")]))
        )
        await server.request(
            FESL, "rank", update_stats((ann, "1", [(3, "score", "4.0000", "", "1")]))
        )

        for period, past, expected in [
            ("0", "0", "10.0000"),
            ("2", "0", "10.0000"),
            ("1", "0", "4.0000"),
            ("4", "0", "0.0000"),
            ("2", "1", "0.0000"),
        ]:
            answer = await server.request(
                FESL, "rank", get_stats(ann, "score", period=period, past=past)
            )
            assert answer["stats.0.value"] == expected, (period, past)

        for _ in range(2):
            added = update_stats(("1", "11", [(3, "kills", "5.0000", "", "0")]))
            assert "errorCode" not in await server.request(FESL, "rank", added)
        whole = await player.request(FESL, "rank", get_stats("1", "kills", owner_type="11"))
        assert whole["stats.0.value"] == "10.0000"


async def test_only_dedicated_servers_that_host_write_and_a_refused_update_changes_nothing(
    server_url: str,
) -> None:
    async with game(server_url) as player, game(server_url) as server:
        ann = await play_as(player, "ann@example.test", "Ann_01")
        cheats = [
            update_stats(("", "1", [(0, "score", "99999.0000", "", "0")])),
            update_stats((ann, "1", [(3, "kills", "500.0000", "", "0")])),
        ]
        for cheat in cheats:
            assert (await player.request(FESL, "rank", cheat))["errorCode"] == "21"
        # Anyone logs in like a dedicated server; hosting a game players see is what counts.
        lkey = await log_in_server(server, FESL)
        assert (await server.request(FESL, "rank", cheats[1]))["errorCode"] == "21"
        await enter_theater(server, THEATER, "bfbc2-pc-server", lkey)
        created = await server.theater(THEATER, "CGAM", CREATE_GAME)
        # A new server waits for the admin's approval and writes nothing until it has it, nor
        # reads its players' stats, which it would otherwise try to upload until it quits.
        assert created.error is None
        loaded = get_stats(ann, "kills")
        assert (await server.request(FESL, "rank", loaded))["errorCode"] == "21"
        played = update_stats((ann, "1", [(3, "kills", "5.0000", "", "0")]))
        assert (await server.request(FESL, "rank", played))["errorCode"] == "21"
        await anyio.to_thread.run_sync(main, ["servers", "approve", created.fields["UGID"]])
        assert (await server.request(FESL, "rank", loaded))["stats.0.value"] == "0.0000"
        assert "errorCode" not in await server.request(FESL, "rank", played)

    async with game(server_url) as server:
        await host(server)
        both = update_stats((ann, "1", [(3, "score", "5.0000", "", "0")]), ("999999", "1", []))
        assert (await server.request(FESL, "rank", both))["errorCode"] == "101"
        for invalid in [(7, "score", "1.0000", "", "0"), (3, "score", "nan", "", "0")]:
            refused = await server.request(FESL, "rank", update_stats((ann, "1", [invalid])))
            assert refused["errorCode"] == "21"
        unchanged = await server.request(FESL, "rank", get_stats(ann, "score"))
        assert unchanged["stats.0.value"] == "0.0000"

    async with game(server_url) as stranger:
        await stranger.hello(FESL, "bfbc2-pc")
        assert (await stranger.request(FESL, "rank", get_stats(ann, "score")))["errorCode"] == "20"


async def test_the_largest_requests_are_answered_and_numbers_beyond_the_sdks_are_refused(
    server_url: str, database_url: str
) -> None:
    async with game(server_url) as server:
        await host(server)
        added = update_stats((str(MAXIMUM_ENTRIES), "11", [(3, "kills", "2.0000", "", "0")]))
        assert "errorCode" not in await server.request(FESL, "rank", added)
        # The connection stays, or the second request would get no answer.
        with anyio.fail_after(10):
            for owner, owner_type in ((str(2**63), "1"), ("1", str(2**31))):
                beyond = get_stats(owner, "kills", owner_type=owner_type)
                assert (await server.request(FESL, "rank", beyond))["errorCode"] == "21"

    engine = create_async_engine(database_url)
    store = SqlStatStore(async_sessionmaker(engine))
    everyone = [Owner(GAME_WIDE, number) for number in range(1, MAXIMUM_ENTRIES + 1)]
    found = await store.values(
        "/eagames/BFBC2", everyone, ["kills"], Bucket(Period.ALL_TIME, date(1970, 1, 1))
    )
    assert found == {(Owner(GAME_WIDE, MAXIMUM_ENTRIES), "kills"): Value(2.0, "")}
    await engine.dispose()


@pytest.mark.parametrize(
    ("period", "today", "past", "start"),
    [
        (Period.DAILY, date(2026, 10, 24), 1, date(2026, 10, 23)),
        (Period.WEEKLY, date(2026, 10, 24), 0, date(2026, 10, 19)),
        (Period.WEEKLY, date(2026, 10, 19), 1, date(2026, 10, 12)),
        (Period.BIWEEKLY, date(2026, 10, 24), 0, date(2026, 10, 12)),
        (Period.BIWEEKLY, date(2026, 10, 24), -1, date(2026, 10, 26)),
        (Period.MONTHLY, date(2026, 10, 24), 10, date(2025, 12, 1)),
        (Period.QUARTERLY, date(2026, 2, 10), 1, date(2025, 10, 1)),
        (Period.QUARTERLY, date(2026, 11, 30), -1, date(2027, 1, 1)),
        (Period.YEARLY, date(2026, 10, 24), 2, date(2024, 1, 1)),
        (Period.ALL_TIME, date(2026, 10, 24), 3, date(1970, 1, 1)),
    ],
)
def test_periods_start_on_mondays_months_quarters_and_years_across_year_ends(
    period: Period, today: date, past: int, start: date
) -> None:
    assert period_start(period, today, past) == start


async def test_leaderboards_rank_everyone_and_show_the_caller_outside_the_window(
    server_url: str,
) -> None:
    async with game(server_url) as player, game(server_url) as server:
        await log_in_player(player, FESL, "ann@example.test")
        names = ("Ann_01", "Bob_02", "Cid_03", "Dee_04")
        ids = {}
        for name in names:
            await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", name)])
            playing = [("TXN", "NuLoginPersona"), ("name", name)]
            ids[name] = (await player.request(FESL, "acct", playing))["userId"]
        ann, bob, cid, dee = (ids[name] for name in names)
        await host(server)
        rounds = [
            (ann, [(3, "score", "300.0000", "", "0"), (3, "deaths", "5.0000", "", "0")]),
            (bob, [(3, "score", "500.0000", "", "0"), (3, "deaths", "9.0000", "", "0")]),
            (cid, [(3, "score", "300.0000", "", "0"), (3, "deaths", "2.0000", "", "0")]),
            (dee, [(3, "score", "100.0000", "", "0")]),
        ]
        await server.request(FESL, "rank", update_stats(*((id, "1", s) for id, s in rounds)))

        mine = await player.request(
            FESL,
            "rank",
            [
                ("TXN", "GetRankedStats"),
                ("owner", ann),
                ("ownerType", "1"),
                ("periodId", "0"),
                ("periodPast", "0"),
                *keys("score", "deaths", "flags"),
                ("rankOrders.0", "0"),
                ("rankOrders.1", "1"),
                ("rankOrders.2", "0"),
                ("rankOrders.[]", "3"),
            ],
        )
        assert ranks(mine) == [
            ("score", "300.0000", "2"),
            ("deaths", "5.0000", "2"),
            ("flags", "0.0000", "0"),
        ]
        friends = await player.request(
            FESL,
            "rank",
            [
                ("TXN", "GetRankedStatsForOwners"),
                ("owners.0.ownerId", bob),
                ("owners.0.ownerType", "1"),
                ("owners.1.ownerId", "999999"),
                ("owners.1.ownerType", "1"),
                ("owners.[]", "2"),
                ("periodId", "0"),
                *keys("score"),
            ],
        )
        assert friends["rankedStats.1.ownerId"] == "999999"
        assert ranks(friends, "rankedStats.0.rankedStats.") == [("score", "500.0000", "1")]
        assert ranks(friends, "rankedStats.1.rankedStats.") == [("score", "0.0000", "0")]

        # Ann and Cid tie for 2nd across the pages' boundary; each page lists one of them.
        pages = [
            leaders(await player.request(FESL, "rank", top_n("GetTopN", "score", first, first + 1)))
            for first in (1, 3)
        ]
        assert pages == [
            [("Bob_02", "1", "500.0000"), ("Ann_01", "2", "300.0000")],
            [("Cid_03", "2", "300.0000"), ("Dee_04", "4", "100.0000")],
        ]
        await player.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "Dee_04")])
        with_me = await player.request(
            FESL, "rank", [*top_n("GetTopNAndMe", "score", 1, 2), ("includeUser", "1")]
        )
        assert leaders(with_me)[2] == ("Dee_04", "4", "100.0000")
        assert with_me["stats.2.owner"] == dee

        rest = await player.request(
            FESL, "rank", [*top_n("GetTopNAndStats", "score", 2, 10), *keys("deaths")]
        )
        assert leaders(rest) == [
            ("Ann_01", "2", "300.0000"),
            ("Cid_03", "2", "300.0000"),
            ("Dee_04", "4", "100.0000"),
        ]
        assert rest["stats.0.addStats.[]"] == "1"
        assert [rest[f"stats.{index}.addStats.0.value"] for index in range(3)] == [
            "5.0000",
            "2.0000",
            "0.0000",
        ]
        fewest = await player.request(FESL, "rank", top_n("GetTopN", "deaths", 1, 10, order="1"))
        assert [name for name, _, _ in leaders(fewest)] == ["Cid_03", "Ann_01", "Bob_02"]

        # A deleted soldier leaves the leaderboards and everyone behind it moves up.
        await player.request(FESL, "acct", [("TXN", "NuDisablePersona"), ("name", "Bob_02")])
        board = await player.request(FESL, "rank", top_n("GetTopN", "score", 1, 10))
        assert leaders(board) == [
            ("Ann_01", "1", "300.0000"),
            ("Cid_03", "1", "300.0000"),
            ("Dee_04", "3", "100.0000"),
        ]
        ranked = [("TXN", "GetRankedStats"), ("periodId", "0"), *keys("score")]
        assert ranks(await player.request(FESL, "rank", ranked)) == [("score", "100.0000", "3")]


async def test_date_ranges_span_the_current_period(server_url: str) -> None:
    async with game(server_url) as player:
        await log_in_player(player, FESL, "ann@example.test")
        week = await player.request(
            FESL, "rank", [("TXN", "GetDateRange"), ("key", "score"), ("periodId", "2")]
        )
        start, end = (fesl_time(week[name]) for name in ("startDate", "endDate"))
        assert (start.weekday(), start.time()) == (0, time())
        assert end - start == timedelta(days=7, seconds=-1)
        ever = await player.request(FESL, "rank", [("TXN", "GetDateRange"), ("periodId", "0")])
        assert fesl_time(ever["startDate"]) == datetime(1970, 1, 1)


def top_n(txn: str, key: str, first: int, last: int, order: str = "0") -> Fields:
    return [
        ("TXN", txn),
        ("key", key),
        ("ownerType", "1"),
        ("minRank", str(first)),
        ("maxRank", str(last)),
        ("periodId", "0"),
        ("periodPast", "0"),
        ("rankOrder", order),
    ]


def ranks(answer: dict[str, str], prefix: str = "stats.") -> list[tuple[str, str, str]]:
    return [
        (key, value, answer[f"{prefix}{index}.rank"])
        for index, (key, value) in enumerate(values(answer, prefix))
    ]


def leaders(answer: dict[str, str]) -> list[tuple[str, str, str]]:
    return [
        (
            answer[f"stats.{index}.name"],
            answer[f"stats.{index}.rank"],
            answer[f"stats.{index}.value"],
        )
        for index in range(int(answer["stats.[]"]))
    ]


def fesl_time(text: str) -> datetime:
    return datetime.strptime(text, "%b-%d-%Y %H:%M:%S UTC")
