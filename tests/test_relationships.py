from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree

import pytest
from accounts import execute
from heroes import MAGMA
from social import ANN, BOB, Player, entry, name_accounts, playing, post, refusal, roster
from support import fetch

pytestmark = pytest.mark.anyio


def change(player: Player, verb: str, other: int, tag: str = "friends") -> str:
    """Return the path of a change of the player's relationship with another account."""
    return f"/relationships/{verb}/nucleus:{player[1]}/nucleus:{other}/{tag}"


def status(line: str) -> bytes:
    """Return a status update as 1.89 posts it."""
    return f"<update>\n\t<status><![CDATA[{line}]]></status>\n</update>".encode()


async def test_a_request_once_accepted_lists_each_account_on_the_others_roster_with_presence(
    base: str, server_url: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    async with playing(server_url, ANN) as [ann]:
        async with playing(server_url, BOB) as [bob]:
            before = datetime.now(UTC).replace(microsecond=0)
            reports = [
                await post(
                    server_url, ann, f"/relationships/status/nucleus:{ann[1]}", status("Ann")
                ),
                # 1.46 writes its line bare.
                await post(
                    server_url,
                    bob,
                    f"/relationships/status/nucleus:{bob[1]}",
                    b"<update>\n\t<status>Bob in a match</status>\n</update>",
                ),
            ]
            asked = await post(server_url, ann, change(ann, "increase", bob[1]))
            # A request is new to the account it was sent to, which sees no presence yet.
            [request] = await roster(server_url, bob)
            seen = await post(
                server_url, bob, f"/relationships/acknowledge/nucleus:{bob[1]}/{request['id']}"
            )
            [acknowledged] = await roster(server_url, bob)
            accepted = await post(server_url, bob, change(bob, "increase", ann[1]))
            [friend] = await roster(server_url, ann)
            [friend_of_bob] = await roster(server_url, bob)
        # Once Bob's game ends, Ann's game reads him as gone.
        [gone] = await roster(server_url, ann)
        # A ban whose time has passed lets Bob log in again, and leaves him on Ann's roster.
        ended = "UPDATE account SET state = 'banned', state_until = now() - interval '1 minute'"
        await execute(database_url, f"{ended} WHERE nuid = :bob", bob=BOB)
        [unbanned] = await roster(server_url, ann)

    assert [report.tag for report in [*reports, seen]] == ["success"] * 3
    [asked_entry] = asked
    assert asked.tag == "success"
    assert {key: entry(asked_entry)[key] for key in ("new", "state", "realid", "type", "name")} == {
        "new": "false",
        "state": "1",
        "realid": str(bob[1]),
        "type": "nucleus",
        "name": "Invented_Bob",
    }
    assert (request["new"], request["state"], request["realid"]) == ("true", "2", str(ann[1]))
    assert (request["name"], request["status"]) == ("Invented_Ann", "")
    assert (acknowledged["id"], acknowledged["new"]) == (request["id"], "false")
    assert entry(accepted[0])["state"] == "3"
    # The account that asked learns of the acceptance, and friends see each other's presence.
    assert (friend["new"], friend["state"], friend["name"]) == ("true", "3", "Invented_Bob")
    assert (friend["status"], friend_of_bob["status"]) == ("Bob in a match", "Ann")
    reported = datetime.strptime(friend["entityModified"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert before <= reported <= datetime.now(UTC)
    assert gone["status"] == ""
    assert unbanned["name"] == "Invented_Bob"
    seen_last = datetime.strptime(gone["entityModified"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert datetime.now(UTC) - seen_last > timedelta(minutes=12)
    # Login names are never told.
    assert not any("@" in value for value in [*friend.values(), *friend_of_bob.values()])


async def test_declining_cancelling_or_removing_ends_a_relationship_on_both_sides(
    base: str, server_url: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    async with playing(server_url, ANN, BOB) as (ann, bob):
        rosters = []
        for ender, other, accept in ((bob, ann, False), (ann, bob, False), (ann, bob, True)):
            await post(server_url, ann, change(ann, "increase", bob[1]))
            if accept:
                await post(server_url, bob, change(bob, "increase", ann[1]))
            ended = await post(server_url, ender, change(ender, "decrease", other[1]))
            assert ended.tag == "success"
            assert entry(ended[0])["state"] == "0"
            rosters += [await roster(server_url, ann), await roster(server_url, bob)]

    # Declined, cancelled and removed: nothing is left on either side.
    assert rosters == [[]] * 6


async def test_changes_the_rules_do_not_allow_are_refused(
    base: str, server_url: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    # An account without a display name is not one that may be asked.
    await execute(database_url, "UPDATE account SET display_name = NULL WHERE nuid = :bob", bob=BOB)
    async with playing(server_url, ANN, BOB) as (ann, bob):
        refusals = [
            refusal(await post(server_url, ann, path))
            for path in (
                change(ann, "increase", ann[1]),
                change(ann, "increase", bob[1]),
                change(ann, "increase", 999999),
                change(ann, "increase", bob[1], tag="enemies"),
                change(ann, "decrease", bob[1]),
                f"/relationships/acknowledge/nucleus:{ann[1]}/999999",
                # Another account's id in the path.
                change(bob, "increase", ann[1]),
                f"/relationships/status/nucleus:{ann[1]}",
            )
        ]
        # Bob, without a display name, asks Ann, and Ann reports a line XML cannot carry and a
        # body far longer than any update, whose tags never close.
        nameless = refusal(await post(server_url, bob, change(bob, "increase", ann[1])))
        reporting = f"/relationships/status/nucleus:{ann[1]}"
        control = refusal(await post(server_url, ann, reporting, status("in\x00menu")))
        flood = refusal(await post(server_url, ann, reporting, b"<status>" * 1000))
        _, _, others = await fetch(
            server_url, f"/relationships/roster/nucleus:{bob[1]}", MAGMA, ann[0]
        )
        _, _, nobody = await fetch(server_url, f"/relationships/roster/nucleus:{ann[1]}", MAGMA)

    assert refusals == [
        ("VALIDATION_FAILED", "SELF_RELATIONSHIP"),
        ("VALIDATION_FAILED", "USER_NOT_FOUND"),
        ("VALIDATION_FAILED", "USER_NOT_FOUND"),
        ("VALIDATION_FAILED", "UNKNOWN_TAG"),
        ("VALIDATION_FAILED", "RELATIONSHIP_NOT_FOUND"),
        ("VALIDATION_FAILED", "RELATIONSHIP_NOT_FOUND"),
        ("VALIDATION_FAILED", "USER_DOES_NOT_MATCH"),
        ("VALIDATION_FAILED", "NO_STATUS"),
    ]
    assert refusal(ElementTree.fromstring(others)) == ("VALIDATION_FAILED", "USER_DOES_NOT_MATCH")
    assert nameless == ("VALIDATION_FAILED", "NO_DISPLAY_NAME")
    assert control == ("VALIDATION_FAILED", "STATUS_NOT_TEXT")
    assert flood == ("VALIDATION_FAILED", "STATUS_TOO_LONG")
    assert refusal(ElementTree.fromstring(nobody)) == ("NOT_LOGGED_IN", "NO_SESSION")


async def test_an_account_with_the_most_friends_can_neither_ask_nor_accept_another(
    base: str, server_url: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    # Ann has asked 100 other accounts already; requests sent to her take no room.
    await execute(
        database_url,
        "INSERT INTO account (nuid, country, server, display_name)"
        " SELECT 'player' || n || '@example.test', 'PL', false, 'Invented_' || n"
        " FROM generate_series(1, 100) AS n",
    )
    await execute(
        database_url,
        "INSERT INTO relationship"
        " (game, tag, owner_id, member_id, owner_agrees, member_agrees, new)"
        " SELECT 'bfheroes', 'friends', CASE WHEN mine THEN ann.id ELSE other.id END,"
        " CASE WHEN mine THEN other.id ELSE ann.id END, mine, NOT mine, NOT mine"
        " FROM account AS ann, account AS other, (VALUES (true), (false)) AS sides (mine)"
        " WHERE ann.nuid = :ann AND other.nuid LIKE 'player%'",
        ann=ANN,
    )
    async with playing(server_url, ANN, BOB) as (ann, bob):
        asking = await post(server_url, ann, change(ann, "increase", bob[1]))
        asked = await post(server_url, bob, change(bob, "increase", ann[1]))
        accepting = await post(server_url, ann, change(ann, "increase", bob[1]))
        listed = await roster(server_url, ann)

    assert refusal(asking) == ("VALIDATION_FAILED", "LIST_FULL")
    assert entry(asked[0])["state"] == "1"
    assert refusal(accepting) == ("VALIDATION_FAILED", "LIST_FULL")
    assert len(listed) == 101
