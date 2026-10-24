import pytest
from support import FESL, Answer, Game, enter_as_player, game, host_game, let_in, text

from openfesl.protocol.relay import Data

pytestmark = pytest.mark.anyio

VERSION = [("filter-version", "ROMEPC795745"), ("filterToGame-version", "version")]
LEVEL = [("pref-level", "levels/mp_001"), ("prefToGame-level", "U-level")]


async def play_now(
    player: Game, session_type: str, *props: tuple[str, str]
) -> tuple[Answer, Answer]:
    """Start a search as the game's SDK writes it and return the answer and the Status."""
    searched = [("sessionType", session_type), ("name", "Ann_01"), ("firewallType", "unknown")]
    searched += props
    fields = [
        ("TXN", "Start"),
        ("partition.partition", "/eagames/BFBC2"),
        ("debugLevel", "off"),
        ("version", "1"),
        ("players.[]", "1"),
        ("players.0.ownerId", "1"),
        ("players.0.ownerType", "1"),
        *((f"players.0.props.{{{key}}}", value) for key, value in searched),
        ("players.0.props.{}", str(len(searched))),
    ]
    answer = await player.request(FESL, "pnow", fields)
    status = await player.push("Status")
    player.frames = [
        frame
        for frame in player.frames
        if not (isinstance(frame, Data) and text(frame.packet).get("TXN") == "Status")
    ]
    assert (status["id.id"], status["id.partition"]) == (answer["id.id"], "/eagames/BFBC2")
    assert status["sessionState"] == "COMPLETE"
    return answer, status


def found(status: Answer) -> list[tuple[str, str]]:
    count = int(status["props.{games}.[]"])
    return [
        (status[f"props.{{games}}.{n}.gid"], status[f"props.{{games}}.{n}.fit"])
        for n in range(count)
    ]


async def test_play_now_finds_the_open_games_that_fit_a_player_best(
    open_hosting: None, server_url: str
) -> None:
    async with (
        game(server_url) as first,
        game(server_url) as second,
        game(server_url) as closed,
        game(server_url) as ann,
        game(server_url) as bob,
    ):
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        _, nothing = await play_now(ann, "findServer", *VERSION)
        assert nothing["props.{resultType}"] == "NOSERVER"

        preferred = await host_game(first, {"B-U-level": "levels/mp_001"})
        fuller = await host_game(
            second, {"NAME": "Invented Server #2", "B-U-level": "levels/mp_002"}
        )
        await host_game(closed, {"NAME": "Invented Server #3", "JOIN": "C"})
        await enter_as_player(bob, "bob@example.test", "Bob_02")
        await let_in(second, bob, fuller)

        first_answer, best = await play_now(ann, "findServer", *VERSION, *LEVEL)
        assert best["props.{resultType}"] == "JOIN"
        assert found(best) == [(preferred["GID"], "1.0000"), (fuller["GID"], "0.0000")]
        assert best["props.{availableServerCount}"] == "2"
        assert (best["props.{games}.0.lid"], best["props.{avgFit}"]) == (preferred["LID"], "0.5000")

        second_answer, together = await play_now(ann, "findServer", *VERSION)
        assert second_answer["id.id"] != first_answer["id.id"]
        assert [gid for gid, _ in found(together)] == [fuller["GID"], preferred["GID"]]
        _, listed = await play_now(ann, "listServers", ("maxListServersResult", "1"))
        assert (listed["props.{resultType}"], len(found(listed))) == ("LIST", 1)
        newer = [("filter-version", "ROMEPC999999"), ("filterToGame-version", "version")]
        _, no_match = await play_now(ann, "findServer", *newer)
        assert no_match["props.{resultType}"] == "NOMATCH"

        cancelled = await ann.request(
            FESL,
            "pnow",
            [
                ("TXN", "Cancel"),
                ("id.id", first_answer["id.id"]),
                ("id.partition", "/eagames/BFBC2"),
            ],
        )
        assert "errorCode" not in cancelled


async def test_play_now_leaves_out_the_servers_that_wait_for_approval(server_url: str) -> None:
    async with game(server_url) as server, game(server_url) as ann:
        await host_game(server)
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        _, nothing = await play_now(ann, "findServer")
        assert nothing["props.{resultType}"] == "NOSERVER"
