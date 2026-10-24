from datetime import date

import anyio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import Game, game, log_in_player, login

from openfesl.core.stats import GAME_WIDE, MAXIMUM_ENTRIES, Bucket, Owner, Value, period_start
from openfesl.protocol.fields import Fields
from openfesl.protocol.rank import Period
from openfesl.protocol.relay import Channel, ConnectionKey
from openfesl.storage.stats import SqlStatStore

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)

type Update = tuple[int, str, str, str, str]


async def play_as(player: Game, nuid: str, name: str) -> str:
    await log_in_player(player, FESL, nuid)
    await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", name)])
    playing = await player.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", name)])
    return playing["userId"]


async def host(server: Game) -> None:
    await server.hello(FESL, "bfbc2-pc-server", client_type="server")
    await server.request(FESL, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
    await server.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "bfbc2.server.p")])


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


async def test_only_dedicated_servers_write_and_a_refused_update_changes_nothing(
    server_url: str,
) -> None:
    async with game(server_url) as player, game(server_url) as server:
        ann = await play_as(player, "ann@example.test", "Ann_01")
        for cheat in [
            update_stats(("", "1", [(0, "score", "99999.0000", "", "0")])),
            update_stats((ann, "1", [(3, "kills", "500.0000", "", "0")])),
        ]:
            assert (await player.request(FESL, "rank", cheat))["errorCode"] == "21"

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
