from contextlib import AsyncExitStack

import anyio
import pytest
from support import (
    CREATE_GAME,
    FESL,
    THEATER,
    Answer,
    Game,
    enter_as_player,
    game,
    holds,
    host_game,
    ids,
    let_in,
    log_in_server,
    theater_answer,
)

from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Data
from openfesl.protocol.theater import MAXIMUM_PACKET_SIZE
from openfesl.relay import endpoint

pytestmark = pytest.mark.anyio


async def list_games(player: Game, *filters: tuple[str, str]) -> tuple[Answer, list[Answer]]:
    """List the lobby's games and take the GDAT rows that follow the answer."""
    listed = await player.theater(THEATER, "GLST", [("LID", "1"), ("TYPE", "G"), *filters])
    assert listed.error is None
    rows = []
    for _ in range(int(listed.fields["NUM-GAMES"])):
        row = await player.theater_push(THEATER, "GDAT")
        assert row.fields["TID"] == listed.fields["TID"]
        rows.append(row.fields)
    return listed.fields, rows


async def names(player: Game, *filters: tuple[str, str]) -> list[str]:
    return [row["N"] for row in (await list_games(player, *filters))[1]]


async def test_players_browse_filter_and_look_up_the_games_of_a_lobby(
    open_hosting: None, server_url: str
) -> None:
    async with (
        game(server_url) as first,
        game(server_url) as second,
        game(server_url) as ann,
        game(server_url) as bob,
    ):
        open_game = await host_game(first, {"B-U-level": "levels/mp_001"})
        closed = {
            "NAME": "Invented Server #2",
            "JOIN": "C",
            "PASSWORD": "1",
            "B-U-level": "levels/mp_002",
            "B-U-PROVIDER": "forged",
        }
        await host_game(second, closed)
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        playgroup = {**dict(CREATE_GAME), "TYPE": "P", "NAME": "Invented Playgroup"}
        assert (await ann.theater(THEATER, "CGAM", list(playgroup.items()))).error is None
        await enter_as_player(bob, "bob@example.test", "Bob_02")
        bob_pid = await let_in(first, bob, open_game)
        opened = await first.theater(THEATER, "UBRA", [*ids(open_game), ("START", "1")])
        bracket = [("TID", opened.fields["TID"])]
        player = [*ids(open_game), ("PID", bob_pid)]
        level = ("B-U-LEVEL", "levels/mp_003")
        await first.send_theater(THEATER, "UGAM", [*ids(open_game), level, *bracket])
        await first.send_theater(THEATER, "UGDE", [*ids(open_game), ("D-pdat00", "|1|"), *bracket])
        await first.send_theater(THEATER, "UPLA", [*player, ("P-kills", "3"), *bracket])
        await first.theater(THEATER, "UBRA", [*ids(open_game), ("START", "0")])

        listed, rows = await list_games(ann, ("COUNT", "-1"))
        assert (listed["NUM-GAMES"], listed["LOBBY-NUM-GAMES"], listed["LOBBY-MAX-GAMES"]) == (
            "2",
            "2",
            "10000",
        )
        assert holds(
            rows[0],
            {
                "N": "Invented Server #1",
                "I": "127.0.0.1",
                "P": "19567",
                "MP": "32",
                "AP": "1",
                "JP": "0",
                "QP": "0",
                "HN": "bfbc2.server.p",
                "PL": "PC",
                "J": "O",
                "PW": "0",
                "B-version": "ROMEPC795745",
                "B-U-level": "levels/mp_003",
            },
        )
        keys = (rows[0] | rows[1]).keys()
        assert not {"SECRET", "EKEY", "UGID", "INT-IP", "B-U-LEVEL"} & keys
        assert not [key for key in keys if key.casefold() == "b-u-provider"]
        assert (rows[1]["J"], rows[1]["PW"]) == ("C", "1")

        assert await names(ann, ("FILTER-NOT-CLOSED", "1")) == ["Invented Server #1"]
        assert await names(ann, ("FILTER-NOT-PRIVATE", "1")) == ["Invented Server #1"]
        assert await names(ann, ("FILTER-MIN-SIZE", "1")) == ["Invented Server #1"]
        level = ("FILTER-ATTR-U-level", "levels/mp_002")
        assert await names(ann, level) == ["Invented Server #2"]
        assert await names(ann, ("FILTER-ATTR-U-gamemode", "rush")) == []
        assert await names(ann, ("COUNT", "1")) == ["Invented Server #1"]
        assert await names(ann, ("GID", rows[1]["GID"])) == ["Invented Server #2"]
        favourites = [("FAV-PLAYER", "Cid_03;bob_02"), ("FAV-GAME", "Invented Server #2")]
        _, marked = await list_games(ann, *favourites)
        assert [(row["F"], row["NF"]) for row in marked] == [("0", "1"), ("1", "0")]
        only = await names(ann, ("FILTER-FAV-ONLY", "1"), ("FAV-PLAYER", "Bob_02"))
        assert only == ["Invented Server #1"]

        lobbies = [("FILTER-NOT-CLOSED", "1"), *favourites]
        assert (await ann.theater(THEATER, "LLST", lobbies)).fields["NUM-LOBBIES"] == "1"
        lobby = (await ann.theater_push(THEATER, "LDAT")).fields
        assert holds(
            lobby,
            {"PASSING": "1", "NUM-GAMES": "2", "FAVORITE-GAMES": "1", "FAVORITE-PLAYERS": "1"},
        )

        looked_up = await ann.theater(THEATER, "GDAT", ids(open_game))
        assert (looked_up.error, looked_up.fields["AP"]) == (None, "1")
        details = (await ann.theater_push(THEATER, "GDET")).fields
        assert holds(details, {"UGID": open_game["UGID"], "D-pdat00": "|1|"})
        assert details["TID"] == looked_up.fields["TID"]
        shown = (await ann.theater_push(THEATER, "PDAT")).fields
        assert holds(shown, {"PID": bob_pid, "NAME": "Bob_02", "P-kills": "3"})
        by_name = await ann.theater(THEATER, "GDAT", [("USER", "bob_02"), ("TYPE", "G")])
        assert by_name.fields["GID"] == open_game["GID"]
        missing = await ann.theater(THEATER, "GDAT", [("LID", "1"), ("GID", "999")])
        assert missing.error == "ngam"
        for lid in ("1", "-1"):
            counted = await ann.theater(THEATER, "PCNT", [("LID", lid)])
            assert (counted.fields["COUNT"], counted.fields["LID"]) == ("1", lid)


async def next_row(player: Game) -> Packet:
    while True:
        frame = await player.next_frame()
        if isinstance(frame, Data) and frame.packet.command == "GDAT":
            return frame.packet


async def test_a_host_that_fills_every_field_still_fits_its_row_in_the_games_buffer(
    open_hosting: None, server_url: str
) -> None:
    # Each % travels as three bytes.
    escaped = "%" * 300
    async with game(server_url) as server, game(server_url) as player:
        lkey = await log_in_server(server, FESL)
        await server.open(THEATER, "bfbc2-pc-server.theater.ea.com", port=18326)
        connect = [("PROT", "2"), ("VERS", escaped), ("PLAT", escaped)]
        assert (await server.theater(THEATER, "CONN", connect)).error is None
        assert (await server.theater(THEATER, "USER", [("LKEY", lkey)])).error is None
        sent = {**dict(CREATE_GAME), "NAME": escaped, "B-version": escaped, "INT-IP": escaped}
        created = (await server.theater(THEATER, "CGAM", list(sent.items()))).fields
        for index in range(30):
            change = [*ids(created), (f"B-U-k{index:02}", "%" * 120), ("TID", "0")]
            await server.send_theater(THEATER, "UGAM", change)
        closed = await server.theater(THEATER, "UBRA", [*ids(created), ("START", "0")])
        assert closed.error is None

        await enter_as_player(player, "ann@example.test", "Ann_01")
        listed = await player.theater(THEATER, "GLST", [("LID", "1"), ("TYPE", "G")])
        assert listed.fields["NUM-GAMES"] == "1"
        with anyio.fail_after(5):
            row = await next_row(player)
        assert len(row.encode()) <= MAXIMUM_PACKET_SIZE
        fields = theater_answer(row).fields
        assert (fields["N"], fields["V"], fields["PL"], fields["B-version"]) == (
            "%" * 127,
            "%" * 63,
            "%" * 31,
            "%" * 63,
        )
        assert sum(key.startswith("B-U-k") for key in fields) > 10


async def test_a_long_game_list_waits_for_the_hook_instead_of_ending_the_session(
    open_hosting: None, server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One list overflows a queue this short unless its rows wait for room.
    monkeypatch.setattr(endpoint, "SEND_LIMIT", 6)
    async with AsyncExitStack() as stack:
        for _ in range(7):
            await host_game(await stack.enter_async_context(game(server_url)))
        player = await stack.enter_async_context(game(server_url))
        await enter_as_player(player, "ann@example.test", "Ann_01")

        listed, rows = await list_games(player, ("COUNT", "-1"))
        assert (listed["NUM-GAMES"], len({row["GID"] for row in rows})) == ("7", 7)
