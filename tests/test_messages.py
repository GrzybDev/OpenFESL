import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import Answer, Game, block, game, log_in_again, log_in_persona, login

from openfesl.core.associations import OwnedList
from openfesl.core.messages import SEND_BURST, Draft
from openfesl.protocol.fields import Fields
from openfesl.protocol.relay import Channel, ConnectionKey
from openfesl.protocol.xmsg import Purge
from openfesl.storage.associations import SqlAssociationStore
from openfesl.storage.messages import SqlMessageStore
from openfesl.storage.models import MessageRow

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
ARRIVED = "AsyncMessageEvent"
PURGED = "AsyncPurgedEvent"


def send(
    to: list[int],
    message_type: str,
    *attachments: tuple[str, str, str],
    delivery: str = "standard",
    purge: str = "all",
) -> Fields:
    # As the SDK writes it: each count again with its entry, attachments counted after them.
    fields = [("TXN", "SendMessage"), ("messageType", message_type)]
    for index, persona_id in enumerate(to):
        fields += [("to.[]", str(len(to))), (f"to.{index}", str(persona_id))]
    for index, (key, kind, data) in enumerate(attachments):
        name = f"attachments.{index}"
        fields += [(f"{name}.key", key), (f"{name}.type", kind), (f"{name}.data", data)]
    fields.append(("attachments.[]", str(len(attachments))))
    return [*fields, ("expires", "86400"), ("deliveryType", delivery), ("purgeStrategy", purge)]


def ids(txn: str, *message_ids: str) -> Fields:
    fields = [("TXN", txn)]
    for index, message_id in enumerate(message_ids):
        fields += [("messageIds.[]", str(len(message_ids))), (f"messageIds.{index}", message_id)]
    return fields


async def box(player: Game, name: str = "inbox") -> Answer:
    return await player.request(
        FESL, "xmsg", [("TXN", "GetMessages"), ("box", name), ("chunkSize", "0")]
    )


async def test_a_message_reaches_online_recipients_at_once_and_the_others_after_login(
    server_url: str,
) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
        async with game(server_url) as carl:
            carl_id, _ = await log_in_persona(carl, FESL, "carl@example.test", "Carl")

        note = ("note", "text/plain", "Squad up, Bob")
        sent = await ann.request(
            FESL,
            "xmsg",
            send([bob_id, carl_id, 999999], "invite/friend", note, ("tag", "image/x-tag", "AAEC")),
        )
        message_id = sent["messageId"]
        assert message_id != "0"
        assert [sent[f"status.{index}.userId"] for index in range(3)] == [
            str(bob_id),
            str(carl_id),
            "999999",
        ]
        assert [sent[f"status.{index}.error"] for index in range(3)] == ["0", "0", "101"]
        event = await bob.push(ARRIVED)
        assert (event["messageId"], event["messageType"], event["expiration"]) == (
            message_id,
            "invite/friend",
            "86400",
        )
        assert (event["from.id"], event["from.name"]) == (str(ann_id), "Ann")
        assert (event["to.[]"], event["to.0.name"], event["to.1.name"]) == ("2", "Bob", "Carl")
        assert (event["attachments.[]"], event["attachments.0.data"]) == ("2", "Squad up, Bob")
        assert event["timeSent"].endswith(" UTC")

        async with game(server_url) as carl:
            await log_in_again(carl, FESL, "carl@example.test", "Carl")
            inbox = await box(carl)
            assert (inbox["messages.[]"], inbox["messages.0.messageId"]) == ("1", message_id)
            assert (inbox["messages.0.from.name"], inbox["messages.0.attachments.1.data"]) == (
                "Ann",
                "AAEC",
            )
            wanted = [("TXN", "GetMessageAttachments"), ("messageId", message_id)]
            notes = await carl.request(
                FESL, "xmsg", [*wanted, ("keys.[]", "1"), ("keys.0", "note")]
            )
            assert (notes["attachments.[]"], notes["attachments.0.key"]) == ("1", "note")

            # A recipient's purge drops only its own copy; the others keep theirs.
            await carl.request(FESL, "xmsg", ids("PurgeMessages", message_id))
            # The SDK counts these ids under a key without the dot of its other lists.
            purged = await carl.push(PURGED)
            assert (purged["messageIds[]"], purged["messageIds.0"]) == ("1", message_id)
            assert (await box(carl))["messages.[]"] == "0"
            unknown = await carl.request(FESL, "xmsg", wanted)
            assert unknown["errorCode"] == "104"

        assert bob.pushed(PURGED) is None
        assert (await box(bob))["messages.0.messageId"] == message_id
        await bob.request(FESL, "xmsg", ids("DeleteMessages", message_id))
        assert (await bob.push(PURGED))["messageIds.0"] == message_id
        # Under the all strategy the sender's copy stays until the sender drops it.
        outbox = await box(ann, "outbox")
        assert (outbox["messages.[]"], outbox["messages.0.to.[]"]) == ("1", "2")
        assert ann.pushed(PURGED) is None
        await ann.request(FESL, "xmsg", ids("DeleteMessages", message_id))
        assert (await ann.push(PURGED))["messageIds.0"] == message_id


async def test_settings_strategies_and_direct_delivery_decide_who_holds_a_message(
    server_url: str,
) -> None:
    async with game(server_url) as ann:
        await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        async with game(server_url) as carl:
            carl_id, _ = await log_in_persona(carl, FESL, "carl@example.test", "Carl")
        async with game(server_url) as bob:
            bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
            # As the SDK writes it, counting the attachment types by the message types.
            settings = [
                ("TXN", "ModifySettings"),
                ("retrieveMessageTypes.[]", "1"),
                ("retrieveMessageTypes.0", "invite/game"),
                ("retrieveAttachmentTypes.[]", "1"),
                ("retrieveAttachmentTypes.0", "application/x-none"),
                ("retrieveAttachmentTypes.[]", "1"),
                ("retrieveAttachmentTypes.1", "text/plain"),
                ("notifyMessages", "1"),
            ]
            await bob.request(FESL, "xmsg", settings)

            friend = await ann.request(
                FESL, "xmsg", send([bob_id], "invite/friend", purge="all-recipients")
            )
            server = ("server", "text/plain", "Arica Harbor")
            invite = await ann.request(
                FESL, "xmsg", send([bob_id], "invite/game", server, ("tag", "image/x-tag", "AAEC"))
            )
            # The friend invitation is not for Bob's ears, or it would have come first.
            event = await bob.push(ARRIVED)
            assert event["messageId"] == invite["messageId"]
            assert (event["attachments.[]"], event["attachments.0.key"]) == ("1", "server")
            listed = await box(bob)
            assert listed["messages.[]"] == "2"

            direct = await ann.request(
                FESL, "xmsg", send([bob_id, carl_id], "invite/game", delivery="direct")
            )
            assert (direct["messageId"], direct["status.0.error"], direct["status.1.error"]) == (
                "0",
                "0",
                "101",
            )
            assert (await bob.push(ARRIVED))["messageId"] == "0"

            await ann.request(FESL, "xmsg", ids("PurgeMessages", invite["messageId"]))
            assert (await ann.push(PURGED))["messageIds.0"] == invite["messageId"]
            assert (await bob.push(PURGED))["messageIds.0"] == invite["messageId"]

            # The sender's copy goes with the last recipient's under all-recipients.
            await bob.request(FESL, "xmsg", ids("DeleteMessages", friend["messageId"]))
            assert (await ann.push(PURGED))["messageIds.0"] == friend["messageId"]
            assert (await box(ann, "outbox"))["messages.[]"] == "0"

            fleeting = await ann.request(
                FESL, "xmsg", send([bob_id], "invite/game", purge="on-logoff")
            )
            assert (await bob.push(ARRIVED))["messageId"] == fleeting["messageId"]

        assert (await ann.push(PURGED))["messageIds.0"] == fleeting["messageId"]
        async with game(server_url) as bob:
            await log_in_again(bob, FESL, "bob@example.test", "Bob")
            assert (await box(bob))["messages.[]"] == "0"
            odd = await bob.request(FESL, "xmsg", send([bob_id], "invite/game", purge="never"))
            assert odd["errorCode"] == "21"
        # Every dedicated server of the game shares its persona, so none of them sends.
        async with game(server_url) as dedicated:
            await dedicated.hello(FESL, "bfbc2-pc-server", client_type="server")
            await dedicated.request(FESL, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
            await dedicated.request(
                FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "bfbc2.server.p")]
            )
            refused = await dedicated.request(FESL, "xmsg", send([bob_id], "invite/game"))
            assert refused["errorCode"] == "21"


async def test_a_sender_sends_a_burst_and_reaches_nobody_who_blocked_it(server_url: str) -> None:
    async with game(server_url) as ann:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        async with game(server_url) as bob:
            bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
        async with game(server_url) as carl:
            carl_id, _ = await log_in_persona(carl, FESL, "carl@example.test", "Carl")
            await carl.request(FESL, "asso", block(carl_id, ann_id))

        sent = await ann.request(FESL, "xmsg", send([carl_id, bob_id], "invite/game"))
        assert (sent["status.0.error"], sent["status.1.error"]) == ("101", "0")
        async with game(server_url) as carl:
            await log_in_again(carl, FESL, "carl@example.test", "Carl")
            assert (await box(carl))["messages.[]"] == "0"

        for _ in range(SEND_BURST - 1):
            assert "errorCode" not in await ann.request(FESL, "xmsg", send([bob_id], "chat"))
        flooding = await ann.request(FESL, "xmsg", send([bob_id], "chat"))
        assert flooding["errorCode"] == "21"


async def test_full_boxes_drop_their_oldest_copies_and_expired_messages_go(
    server_url: str, database_url: str
) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
    engine = create_async_engine(database_url)
    store = SqlMessageStore(async_sessionmaker(engine))

    def draft(
        sender: int, recipient: int, purge: Purge = "all-recipients", lifetime: int = 60
    ) -> Draft:
        return Draft(sender, "invite/game", purge, lifetime, [], [recipient])

    first, gone = await store.send(draft(ann_id, bob_id), 2)
    second, _ = await store.send(draft(ann_id, bob_id), 2)
    assert gone == {}
    third, gone = await store.send(draft(ann_id, bob_id), 2)
    assert gone == {bob_id: [first.id], ann_id: [first.id]}
    inbox = await store.messages(bob_id, outbox=False)
    assert [message.id for message in inbox] == [second.id, third.id]

    # A sender's outbox holds as many, also of messages that every recipient dropped.
    kept, _ = await store.send(draft(bob_id, ann_id, "all"), 2)
    await store.delete(ann_id, [kept.id], everywhere=False)
    await store.send(draft(bob_id, ann_id, "all"), 2)
    _, gone = await store.send(draft(bob_id, ann_id, "all"), 2)
    assert gone == {bob_id: [kept.id]}

    expired, _ = await store.send(draft(ann_id, bob_id, lifetime=0), 3)
    assert len(await store.messages(bob_id, outbox=False)) == 2
    await store.send(draft(ann_id, bob_id), 3)
    async with engine.connect() as connection:
        assert (
            await connection.scalar(select(MessageRow).where(MessageRow.id == expired.id)) is None
        )

    # A logoff drops only what arrived before it, so a quick new login keeps what came since.
    fleeting, _ = await store.send(draft(ann_id, bob_id, "on-logoff"), 4)
    assert await store.log_off(bob_id, fleeting.sent - timedelta(seconds=1)) == {}
    gone = await store.log_off(bob_id, fleeting.sent)
    assert gone == {bob_id: [fleeting.id], ann_id: [fleeting.id]}
    await engine.dispose()


async def test_two_players_changing_what_they_share_at_once_never_wait_on_each_other(
    server_url: str, database_url: str
) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
    engine = create_async_engine(database_url)
    store = SqlMessageStore(async_sessionmaker(engine))
    # Each drop holds a message the other one needs, which deadlocked once.
    for _ in range(10):
        invite, _ = await store.send(Draft(ann_id, "invite/friend", "all", 60, [], [bob_id]), 100)
        answer, _ = await store.send(
            Draft(bob_id, "inviteResp/friend", "all", 60, [], [ann_id]), 100
        )
        both = [invite.id, answer.id]
        await asyncio.gather(
            store.delete(ann_id, both, everywhere=False),
            store.delete(bob_id, both, everywhere=False),
        )
        async with engine.connect() as connection:
            left = await connection.scalar(select(func.count()).where(MessageRow.id.in_(both)))
        assert left == 0

    # A friend add locks both personas, which a message between them refers to.
    lists = SqlAssociationStore(async_sessionmaker(engine))
    friends = OwnedList(ann_id, "PlasmaFriends", "")
    for _ in range(50):
        await asyncio.gather(
            lists.add(friends, bob_id, 20, roll=False, mutual=True),
            store.send(Draft(bob_id, "invite/friend", "all", 60, [], [ann_id]), 100),
            lists.delete(friends, bob_id, mutual=True),
        )
    await engine.dispose()
