import anyio
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import (
    CREATE_GAME,
    Game,
    enter_theater,
    game,
    log_in_persona,
    log_in_player,
    log_in_server,
    login,
)

from openfesl.core.associations import OwnedList
from openfesl.core.messages import Draft
from openfesl.protocol.fields import Fields
from openfesl.protocol.relay import Channel, ConnectionKey
from openfesl.server.cli import main
from openfesl.storage.associations import SqlAssociationStore
from openfesl.storage.messages import SqlMessageStore
from openfesl.storage.personas import SqlPersonaStore

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)
PARTITION = [
    ("domainPartition.domain", "eagames"),
    ("domainPartition.subDomain", "BFBC2"),
    ("domainPartition.key", ""),
]


def get(txn: str, list_type: str, owner: int, partition: Fields = PARTITION) -> Fields:
    return [
        ("TXN", txn),
        *partition,
        ("type", list_type),
        ("owner.id", str(owner)),
        ("owner.type", "1"),
    ]


def change(
    txn: str,
    list_type: str,
    pairs: list[tuple[int, int]],
    partition: Fields = PARTITION,
    when_full: str = "ReturnError",
) -> Fields:
    name = "addRequests" if txn == "AddAssociations" else "deleteRequests"
    fields = [("TXN", txn), *partition, ("type", list_type)]
    if txn == "AddAssociations":
        fields.append(("listFullBehavior", when_full))
    fields.append((f"{name}.[]", str(len(pairs))))
    for index, (owner, member) in enumerate(pairs):
        fields += [
            (f"{name}.{index}.owner.id", str(owner)),
            (f"{name}.{index}.owner.type", "1"),
            (f"{name}.{index}.member.id", str(member)),
            (f"{name}.{index}.member.type", "1"),
            (f"{name}.{index}.mutual", "1" if list_type == "PlasmaFriends" else "0"),
        ]
    return fields


async def no_push(player: Game, txn: str, owner: int) -> bool:
    # A push sent before this answer would arrive before it on the same connection.
    await player.request(FESL, "asso", get("GetAssociationCount", "PlasmaFriends", owner))
    return player.pushed(txn) is None


async def test_a_full_list_that_makes_room_tells_its_owner_which_member_it_dropped(
    server_url: str,
) -> None:
    async with game(server_url) as ann, game(server_url) as others:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        members = []
        for account in range(3):
            key = ConnectionKey(Channel.FESL, account + 1)
            await log_in_player(others, key, f"player{account}@example.test")
            for number in range(7):
                persona = [("name", f"Player_{account}{number}")]
                await others.request(key, "acct", [("TXN", "NuAddPersona"), *persona])
                playing = await others.request(key, "acct", [("TXN", "NuLoginPersona"), *persona])
                members.append(int(playing["userId"]))

        blocked = await ann.request(
            FESL,
            "asso",
            change(
                "AddAssociations",
                "PlasmaBlock",
                [(ann_id, member) for member in members],
                when_full="RollLeastRecentlyModified",
            ),
        )
        assert [blocked[f"result.{index}.outcome"] for index in range(21)] == ["0"] * 21
        assert blocked["result.20.listSize"] == "20"
        # The push came before the answer, which names only the members added.
        assert ann.pushed("NotifyAssociationUpdate") is not None
        dropped = await ann.push("NotifyAssociationUpdate")
        assert (dropped["operation"], dropped["member.id"], dropped["listSize"]) == (
            "del",
            str(members[0]),
            "19",
        )
        assert ann.pushed("NotifyAssociationUpdate") is None


async def test_friends_are_on_each_others_lists_once_both_agree_and_leave_both_at_once(
    server_url: str,
) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")

        empty = await ann.request(FESL, "asso", get("GetAssociations", "PlasmaFriends", ann_id))
        assert (empty["members.[]"], empty["maxListSize"], empty["owner.name"]) == (
            "0",
            "20",
            "Ann",
        )
        assert (empty["type"], empty["domainPartition.subDomain"]) == ("PlasmaFriends", "BFBC2")

        added = await ann.request(
            FESL, "asso", change("AddAssociations", "PlasmaFriends", [(ann_id, bob_id)])
        )
        assert (added["result.0.outcome"], added["result.0.mutual"]) == ("0", "1")
        assert (added["result.0.listSize"], added["result.0.member.name"]) == ("1", "Bob")
        assert added["result.0.owner.id"] == str(ann_id)
        assert added["result.0.member.created"].endswith(" UTC")
        # Bob has not agreed, so his list stays as it was.
        assert await no_push(bob, "NotifyAssociationUpdate", bob_id)
        listed = await bob.request(FESL, "asso", get("GetAssociations", "PlasmaFriends", bob_id))
        assert listed["members.[]"] == "0"

        agreed = await bob.request(
            FESL, "asso", change("AddAssociations", "PlasmaFriends", [(bob_id, ann_id)])
        )
        assert (agreed["result.0.outcome"], agreed["result.0.listSize"]) == ("0", "1")
        listed = await bob.request(FESL, "asso", get("GetAssociations", "PlasmaFriends", bob_id))
        assert (listed["members.[]"], listed["members.0.id"]) == ("1", str(ann_id))
        assert listed["members.0.modified"].endswith(" UTC")
        # Ann's list holds Bob already.
        assert await no_push(ann, "NotifyAssociationUpdate", ann_id)
        again = await ann.request(
            FESL, "asso", change("AddAssociations", "PlasmaFriends", [(ann_id, bob_id)])
        )
        assert (again["result.0.outcome"], again["result.0.listSize"]) == ("0", "1")
        counted = await ann.request(
            FESL, "asso", get("GetAssociationCount", "PlasmaFriends", ann_id)
        )
        assert (counted["count"], counted["maxListSize"]) == ("1", "20")

        spying = await ann.request(FESL, "asso", get("GetAssociations", "PlasmaFriends", bob_id))
        assert spying["errorCode"] == "23001"
        invalid = await ann.request(
            FESL,
            "asso",
            change("AddAssociations", "PlasmaFriends", [(ann_id, 999999), (ann_id, ann_id)]),
        )
        assert (invalid["result.0.outcome"], invalid["result.1.outcome"]) == ("23002", "23002")
        unknown = await ann.request(
            FESL, "asso", change("AddAssociations", "Rivals", [(ann_id, bob_id)])
        )
        assert unknown["errorCode"] == "21"
        unkept = await ann.request(FESL, "asso", get("GetAssociations", "Rivals", ann_id))
        assert (unkept["members.[]"], unkept["maxListSize"]) == ("0", "0")
        # The game's lists live in no partition or its own, so a player cannot open more.
        elsewhere = [("domainPartition.domain", "eagames"), ("domainPartition.subDomain", "Other")]
        for list_type in ("PlasmaFriends", "PlasmaBlock"):
            opened = await ann.request(
                FESL, "asso", change("AddAssociations", list_type, [(ann_id, bob_id)], elsewhere)
            )
            assert opened["errorCode"] == "21"
            unkept = await ann.request(
                FESL, "asso", get("GetAssociations", list_type, ann_id, elsewhere)
            )
            assert (unkept["members.[]"], unkept["maxListSize"]) == ("0", "0")

        removed = await ann.request(
            FESL, "asso", change("DeleteAssociations", "PlasmaFriends", [(ann_id, bob_id)])
        )
        assert (removed["result.0.outcome"], removed["result.0.listSize"]) == ("0", "0")
        told = await bob.push("NotifyAssociationUpdate")
        assert (told["operation"], told["member.id"], told["listSize"]) == ("del", str(ann_id), "0")
        listed = await bob.request(FESL, "asso", get("GetAssociations", "PlasmaFriends", bob_id))
        assert listed["members.[]"] == "0"
        gone = await ann.request(
            FESL, "asso", change("DeleteAssociations", "PlasmaFriends", [(ann_id, bob_id)])
        )
        assert gone["result.0.outcome"] == "0"
        assert await no_push(bob, "NotifyAssociationUpdate", bob_id)

        # A friend invitation agrees in advance, so accepting it puts both on the lists.
        invitation = [
            ("TXN", "SendMessage"),
            ("messageType", "invite/friend"),
            ("to.[]", "1"),
            ("to.0", str(bob_id)),
            ("attachments.[]", "0"),
            ("expires", "86400"),
            ("deliveryType", "standard"),
            ("purgeStrategy", "all"),
        ]
        await ann.request(FESL, "xmsg", invitation)
        accepted = await bob.request(
            FESL, "asso", change("AddAssociations", "PlasmaFriends", [(bob_id, ann_id)])
        )
        assert accepted["result.0.outcome"] == "0"
        told = await ann.push("NotifyAssociationUpdate")
        assert (told["owner.id"], told["member.id"], told["member.name"]) == (
            str(ann_id),
            str(bob_id),
            "Bob",
        )
        assert (told["operation"], told["listSize"], told["mutual"]) == ("add", "1", "1")
        assert (told["type"], told["domainPartition.domain"]) == ("PlasmaFriends", "eagames")
        listed = await ann.request(FESL, "asso", get("GetAssociations", "PlasmaFriends", ann_id))
        assert (listed["members.[]"], listed["members.0.id"]) == ("1", str(bob_id))


async def test_dedicated_servers_keep_the_dogtags_players_take_and_no_other_lists(
    server_url: str,
) -> None:
    async with game(server_url) as ann, game(server_url) as bob, game(server_url) as server:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
        # Anyone logs in like a dedicated server; a server keeps lists while it hosts a game.
        lkey = await log_in_server(server, FESL)
        early = await server.request(FESL, "asso", get("GetAssociations", "dogtags", ann_id, []))
        assert early["errorCode"] == "23001"
        await anyio.to_thread.run_sync(main, ["hosting", "open"])
        await enter_theater(server, THEATER, "bfbc2-pc-server", lkey)
        assert (await server.theater(THEATER, "CGAM", CREATE_GAME)).error is None

        # The server's dogtag list names no domain partition.
        loaded = await server.request(FESL, "asso", get("GetAssociations", "dogtags", ann_id, []))
        assert (loaded["members.[]"], loaded["maxListSize"], loaded["owner.name"]) == (
            "0",
            "100",
            "Ann",
        )
        taken = await server.request(
            FESL, "asso", change("AddAssociations", "dogtags", [(ann_id, bob_id)], [])
        )
        assert (taken["result.0.outcome"], taken["result.0.mutual"]) == ("0", "0")
        assert (taken["result.0.owner.id"], taken["result.0.listSize"]) == (str(ann_id), "1")
        told = await ann.push("NotifyAssociationUpdate")
        assert (told["type"], told["owner.id"], told["member.name"]) == (
            "dogtags",
            str(ann_id),
            "Bob",
        )
        assert told["mutual"] == "0"
        assert await no_push(bob, "NotifyAssociationUpdate", bob_id)

        loaded = await server.request(FESL, "asso", get("GetAssociations", "dogtags", ann_id, []))
        assert (loaded["members.[]"], loaded["members.0.name"]) == ("1", "Bob")
        # Players read the dogtags the servers keep for them, but change none, not even their own.
        own = await bob.request(FESL, "asso", get("GetAssociations", "dogtags", bob_id, []))
        assert (own["members.[]"], own["maxListSize"]) == ("0", "100")
        for owner, member in ((ann_id, bob_id), (bob_id, ann_id)):
            forged = await bob.request(
                FESL, "asso", change("AddAssociations", "dogtags", [(owner, member)], [])
            )
            assert forged["result.0.outcome"] == "23001"
        for list_type in ("PlasmaFriends", "PlasmaBlock"):
            spied = await server.request(FESL, "asso", get("GetAssociations", list_type, ann_id))
            assert spied["errorCode"] == "23001"
            for txn in ("AddAssociations", "DeleteAssociations"):
                meddled = await server.request(
                    FESL, "asso", change(txn, list_type, [(ann_id, bob_id)])
                )
                assert meddled["result.0.outcome"] == "23001"


async def test_the_lists_the_sdk_loads_at_login_answer_for_players_and_servers(
    server_url: str,
) -> None:
    # The game's login fails when one of these fails to load, so each answers, empty or not,
    # in the domain partition the SDK keeps them in.
    reserved = {
        "PlasmaMute": "20",
        "PlasmaBlock": "20",
        "PlasmaFriends": "20",
        "PlasmaRecentPlayers": "100",
    }
    async with game(server_url) as player, game(server_url) as server:
        player_id, _ = await log_in_persona(player, FESL, "ann@example.test", "Ann")
        await server.hello(FESL, "bfbc2-pc-server", client_type="server")
        await server.request(FESL, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
        persona = [("TXN", "NuLoginPersona"), ("name", "bfbc2.server.p")]
        server_id = int((await server.request(FESL, "acct", persona))["userId"])

        for session, owner in ((player, player_id), (server, server_id)):
            for list_type, size in reserved.items():
                loaded = await session.request(
                    FESL, "asso", get("GetAssociations", list_type, owner, [])
                )
                assert (loaded["type"], loaded["owner.id"]) == (list_type, str(owner))
                assert (loaded["members.[]"], loaded["maxListSize"]) == ("0", size)


async def test_a_full_list_refuses_a_member_or_rolls_off_the_least_recently_modified(
    server_url: str, database_url: str
) -> None:
    async with game(server_url) as player:
        await log_in_player(player, FESL, "ann@example.test")
        ids = []
        for name in ("Ann_01", "Ann_02", "Ann_03", "Ann_04", "Ann_05"):
            await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", name)])
            playing = await player.request(
                FESL, "acct", [("TXN", "NuLoginPersona"), ("name", name)]
            )
            ids.append(int(playing["userId"]))
    first, second, third, fourth, fifth = ids
    engine = create_async_engine(database_url)
    store = SqlAssociationStore(async_sessionmaker(engine))
    recent = OwnedList(first, "PlasmaRecentPlayers", "/eagames/BFBC2")

    await store.add(recent, second, 2, roll=False, mutual=False)
    await store.add(recent, third, 2, roll=False, mutual=False)
    assert await store.add(recent, fourth, 2, roll=False, mutual=False) is None
    await store.add(recent, second, 2, roll=False, mutual=False)
    rolled = await store.add(recent, fourth, 2, roll=True, mutual=False)
    assert rolled is not None
    # The member that made room is a change of its own, so that the owner hears of it.
    assert [(change.member.id, change.removed, change.size) for change in rolled] == [
        (fourth, False, 2),
        (third, True, 1),
    ]
    assert [member.id for member in await store.members(recent)] == [second, fourth]

    # A disabled persona's membership takes no room, so the oldest visible member makes room.
    await SqlPersonaStore(async_sessionmaker(engine)).disable(second)
    await store.add(recent, fifth, 2, roll=False, mutual=False)
    rolled = await store.add(recent, third, 2, roll=True, mutual=False)
    assert rolled is not None
    assert [change.member.id for change in rolled if change.removed] == [fourth]
    assert [member.id for member in await store.members(recent)] == [fifth, third]

    # A member that invited the owner takes it on its own list, so the pair fails whole when
    # that list is full.
    sessions = async_sessionmaker(engine)
    invitation = Draft(fourth, "invite/friend", "all", 60, [], [first])
    await SqlMessageStore(sessions).send(invitation, 100)
    await store.add(OwnedList(fourth, "PlasmaFriends", ""), fifth, 1, roll=False, mutual=False)
    friends = OwnedList(first, "PlasmaFriends", "")
    agreed = await store.add(
        friends, fourth, 1, roll=False, mutual=True, invitations=["invite/friend"]
    )
    assert agreed is None
    assert await store.count(friends) == 0
    await engine.dispose()
