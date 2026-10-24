import pytest
from support import THEATER, Answer, Game, enter_as_player, game, host_game

from openfesl.core import joining

pytestmark = pytest.mark.anyio

# What a player's game sends about itself: its external port and its LAN address.
JOIN = [("PORT", "50111"), ("R-INT-IP", "192.168.1.20"), ("R-INT-PORT", "3659"), ("PTYPE", "P")]


def holds(fields: Answer, expected: Answer) -> bool:
    return fields.items() >= expected.items()


def ids(created: Answer) -> list[tuple[str, str]]:
    return [("LID", created["LID"]), ("GID", created["GID"])]


async def asked_about(server: Game) -> str:
    return (await server.theater_push(THEATER, "EGRQ")).fields["PID"]


async def let_in(server: Game, player: Game, created: Answer) -> str:
    """Join a player to a game that has room, the host allowing it, and report it entered."""
    assert (await player.theater(THEATER, "EGAM", [*ids(created), *JOIN])).error is None
    pid = await asked_about(server)
    allowed = [*ids(created), ("ALLOWED", "1"), ("PID", pid)]
    assert (await server.theater(THEATER, "EGRS", allowed)).error is None
    assert (await player.theater_push(THEATER, "EGEG")).error is None
    assert (await server.theater(THEATER, "PENT", [*ids(created), ("PID", pid)])).error is None
    return pid


async def test_a_player_joins_a_dedicated_server_once_the_host_allows_it(
    open_hosting: None, server_url: str
) -> None:
    async with game(server_url) as server, game(server_url) as ann, game(server_url) as bob:
        created = await host_game(server)
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await enter_as_player(bob, "bob@example.test", "Bob_02")

        # A question the host's game could not read would cost the host its connection.
        too_much = [(f"R-U-k{index}", "x" * 120) for index in range(80)]
        oversized = await ann.theater(THEATER, "EGAM", [*ids(created), *JOIN, *too_much])
        assert oversized.error == "bpar"
        joined = await ann.theater(THEATER, "EGAM", [*ids(created), *JOIN, ("R-U-team", "1")])
        assert (joined.error, joined.fields["LID"], joined.fields["GID"]) == (
            None,
            created["LID"],
            created["GID"],
        )
        asked = (await server.theater_push(THEATER, "EGRQ")).fields
        assert holds(
            asked,
            {
                "NAME": "Ann_01",
                "IP": "127.0.0.1",
                "PORT": "50111",
                "R-INT-IP": "192.168.1.20",
                "R-INT-PORT": "3659",
                "PTYPE": "P",
                "R-U-team": "1",
            },
        )
        allowed = [*ids(created), ("ALLOWED", "1"), ("PID", asked["PID"])]
        assert (await server.theater(THEATER, "EGRS", allowed)).error is None
        notice = await ann.theater_push(THEATER, "EGEG")
        assert notice.error is None
        assert holds(
            notice.fields,
            {
                "PID": asked["PID"],
                "TICKET": asked["TICKET"],
                "I": "127.0.0.1",
                "P": "19567",
                "INT-IP": "192.168.1.10",
                "INT-PORT": "19567",
                "PL": "PC",
                "EKEY": created["EKEY"],
                "UGID": created["UGID"],
            },
        )
        assert "SECRET" not in notice.fields
        entered = await server.theater(THEATER, "PENT", [*ids(created), ("PID", asked["PID"])])
        assert (entered.error, entered.fields["PID"]) == (None, asked["PID"])
        assert (await server.theater(THEATER, "EGRS", allowed)).error == "ntfn"

        by_name = [("USER", "ann_01"), ("R-USER", "ann_01"), ("TYPE", "G"), *JOIN]
        assert (await bob.theater(THEATER, "EGAM", by_name)).fields["GID"] == created["GID"]
        asked_bob = (await server.theater_push(THEATER, "EGRQ")).fields
        assert (asked_bob["NAME"], asked_bob["R-USER"], asked_bob["R-UID"]) == (
            "Bob_02",
            "Ann_01",
            asked["UID"],
        )
        refused = [*ids(created), ("ALLOWED", "0"), ("PID", asked_bob["PID"]), ("REASON", "4")]
        assert (await server.theater(THEATER, "EGRS", refused)).error is None
        denied = await bob.theater_push(THEATER, "EGEG")
        assert (denied.error, denied.fields["REASON"], "TICKET" in denied.fields) == (
            "jden",
            "4",
            False,
        )
        unknown = await bob.theater(THEATER, "EGAM", [("LID", "1"), ("GID", "999"), *JOIN])
        assert unknown.error == "ngam"

        await server.close(THEATER)
        removed = await ann.theater_push(THEATER, "GREM")
        assert removed.fields == {"LID": created["LID"], "GID": created["GID"]}


async def test_a_full_game_queues_players_and_asks_its_host_about_them_as_places_free(
    open_hosting: None, server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(joining, "HOST_ANSWER_TIMEOUT", 0.2)
    async with (
        game(server_url) as server,
        game(server_url) as ann,
        game(server_url) as bob,
        game(server_url) as cid,
    ):
        created = await host_game(server, {"MAX-PLAYERS": "1", "QLEN": "1"})
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await enter_as_player(bob, "bob@example.test", "Bob_02")
        await enter_as_player(cid, "cid@example.test", "Cid_03")
        ann_pid = await let_in(server, ann, created)

        queued = await bob.theater(THEATER, "EGAM", [*ids(created), *JOIN])
        assert (queued.error, queued.fields["QPOS"], queued.fields["QLEN"]) == ("queu", "0", "1")
        assert queued.fields["GID"] == created["GID"]
        full = await cid.theater(THEATER, "EGAM", [*ids(created), *JOIN])
        assert (full.error, full.fields["REASON"]) == ("jden", "f")

        assert (await ann.theater(THEATER, "ECNL", ids(created))).error is None
        assert (await server.theater_push(THEATER, "KICK")).fields["PID"] == ann_pid
        bob_pid = await asked_about(server)
        timed_out = await bob.theater_push(THEATER, "EGEG")
        assert (timed_out.error, timed_out.fields["REASON"]) == ("jden", "t")
        late = [*ids(created), ("ALLOWED", "1"), ("PID", bob_pid)]
        assert (await server.theater(THEATER, "EGRS", late)).error == "ntfn"

        opened = await server.theater(THEATER, "UBRA", [*ids(created), ("START", "1")])
        bracket = [("TID", opened.fields["TID"])]
        await server.send_theater(THEATER, "UGAM", [*ids(created), ("JOIN", "C"), *bracket])
        await server.theater(THEATER, "UBRA", [*ids(created), ("START", "0")])
        closed = await cid.theater(THEATER, "EGAM", [*ids(created), *JOIN])
        assert (closed.error, closed.fields["REASON"]) == ("jden", "c")


async def test_a_full_game_that_takes_more_players_lets_its_queue_in_first(
    open_hosting: None, server_url: str
) -> None:
    async with (
        game(server_url) as server,
        game(server_url) as ann,
        game(server_url) as bob,
        game(server_url) as cid,
    ):
        created = await host_game(server, {"MAX-PLAYERS": "1", "QLEN": "4"})
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await enter_as_player(bob, "bob@example.test", "Bob_02")
        await enter_as_player(cid, "cid@example.test", "Cid_03")
        await let_in(server, ann, created)
        assert (await bob.theater(THEATER, "EGAM", [*ids(created), *JOIN])).error == "queu"

        opened = await server.theater(THEATER, "UBRA", [*ids(created), ("START", "1")])
        bracket = [("TID", opened.fields["TID"])]
        await server.send_theater(THEATER, "UGAM", [*ids(created), ("MAX-PLAYERS", "3"), *bracket])
        await server.theater(THEATER, "UBRA", [*ids(created), ("START", "0")])
        assert (await cid.theater(THEATER, "EGAM", [*ids(created), *JOIN])).error is None
        asked = [(await server.theater_push(THEATER, "EGRQ")).fields["NAME"] for _ in range(2)]
        assert asked == ["Bob_02", "Cid_03"]


async def test_a_host_that_dequeues_players_itself_hears_who_waits_and_picks_them(
    open_hosting: None, server_url: str
) -> None:
    async with (
        game(server_url) as server,
        game(server_url) as ann,
        game(server_url) as bob,
        game(server_url) as cid,
    ):
        settings = {"MAX-PLAYERS": "1", "QLEN": "2", "DISABLE-AUTO-DEQUEUE": "1"}
        created = await host_game(server, settings)
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await enter_as_player(bob, "bob@example.test", "Bob_02")
        await enter_as_player(cid, "cid@example.test", "Cid_03")
        await let_in(server, ann, created)

        assert (await bob.theater(THEATER, "EGAM", [*ids(created), *JOIN])).error == "queu"
        bob_entry = (await server.theater_push(THEATER, "QENT")).fields
        assert bob_entry["NAME"] == "Bob_02"
        second = await cid.theater(THEATER, "EGAM", [*ids(created), *JOIN])
        assert (second.error, second.fields["QPOS"], second.fields["QLEN"]) == ("queu", "1", "2")
        cid_pid = (await server.theater_push(THEATER, "QENT")).fields["PID"]

        order = [*ids(created), ("QUEUE", f"{cid_pid},{bob_entry['PID']}")]
        assert (await server.theater(THEATER, "UQUE", order)).error is None
        assert (await cid.theater_push(THEATER, "QLEN")).fields["QPOS"] == "0"
        assert (await bob.theater_push(THEATER, "QLEN")).fields["QPOS"] == "1"
        dequeued = await server.theater(THEATER, "DQEG", [*ids(created), ("PID", cid_pid)])
        assert dequeued.error is None
        assert await asked_about(server) == cid_pid
        assert (await bob.theater_push(THEATER, "QLEN")).fields["QPOS"] == "0"

        kick = [*ids(created), ("PID", bob_entry["PID"]), ("REASON", "1")]
        assert (await server.theater(THEATER, "QKCK", kick)).error is None
        sent_away = await bob.theater_push(THEATER, "EGEG")
        assert (sent_away.error, sent_away.fields["REASON"]) == ("jden", "1")
        assert (await bob.theater(THEATER, "EGAM", [*ids(created), *JOIN])).error == "queu"
        again = (await server.theater_push(THEATER, "QENT")).fields["PID"]
        await bob.close(THEATER)
        assert (await server.theater_push(THEATER, "QLVT")).fields["PID"] == again
