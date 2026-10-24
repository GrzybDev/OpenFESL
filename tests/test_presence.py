import asyncio

import pytest
from support import block, game, log_in_persona, log_in_player, login

from openfesl.core.personas import Persona
from openfesl.protocol.fields import Fields, format_fields
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Channel, ConnectionKey, Data, encode_frame
from openfesl.storage.personas import SqlPersonaStore

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
EVENT = "AsyncPresenceStatusEvent"


def users(txn: str, *persona_ids: int) -> Fields:
    fields = [("TXN", txn), ("requests.[]", str(len(persona_ids)))]
    return fields + [
        (f"requests.{index}.userId", str(persona_id))
        for index, persona_id in enumerate(persona_ids)
    ]


def status(show: str, *extra: tuple[str, str]) -> Fields:
    return [("TXN", "SetPresenceStatus"), ("status.show", show), *extra]


async def test_subscribers_follow_a_players_status_until_the_player_leaves(
    server_url: str,
) -> None:
    async with game(server_url) as ann, game(server_url) as carl:
        await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        await log_in_persona(carl, FESL, "carl@example.test", "Carl")
        async with game(server_url) as bob:
            bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")

            answer = await ann.request(FESL, "pres", users("PresenceSubscribe", bob_id, 999999))
            assert answer["responses.[]"] == "2"
            assert (answer["responses.0.owner.id"], answer["responses.0.owner.name"]) == (
                str(bob_id),
                "Bob",
            )
            assert (answer["responses.0.outcome"], answer["responses.1.outcome"]) == ("0", "101")

            playing = status("chat", ("status.status", "Rush, Arica"))
            await bob.request(FESL, "pres", [*playing, ("status.attributes.{joinable}", "true")])
            event = await ann.push(EVENT)
            assert (event["owner.id"], event["owner.name"], event["initial"]) == (
                str(bob_id),
                "Bob",
                "0",
            )
            assert (event["status.show"], event["status.status"]) == ("chat", "Rush, Arica")
            assert event["status.attributes.{joinable}"] == "true"

            await carl.request(FESL, "pres", users("PresenceSubscribe", bob_id))
            # The snapshot follows the answer, since the game drops it before the answer.
            assert carl.pushed(EVENT) is None
            snapshot = await carl.push(EVENT)
            assert (snapshot["initial"], snapshot["status.show"]) == ("1", "chat")

            left = await ann.request(FESL, "pres", users("PresenceUnsubscribe", bob_id))
            assert (left["responses.0.owner.id"], left["responses.0.outcome"]) == (str(bob_id), "0")
            await bob.request(FESL, "pres", status("game"))
            assert (await carl.push(EVENT))["status.show"] == "game"
            # A push to Ann would have reached her before the answer to this request.
            await ann.request(FESL, "pres", users("PresenceUnsubscribe", bob_id))
            assert ann.pushed(EVENT) is None

        offline = await carl.push(EVENT)
        assert (offline["owner.id"], offline["status.show"]) == (str(bob_id), "disc")


async def test_a_player_that_blocked_another_stays_hidden_from_it(server_url: str) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        ann_id, _ = await log_in_persona(ann, FESL, "ann@example.test", "Ann")
        bob_id, _ = await log_in_persona(bob, FESL, "bob@example.test", "Bob")
        assert (await bob.request(FESL, "asso", block(bob_id, ann_id)))["result.0.outcome"] == "0"

        refused = await ann.request(FESL, "pres", users("PresenceSubscribe", bob_id))
        assert (refused["responses.0.owner.id"], refused["responses.0.outcome"]) == (
            str(bob_id),
            "101",
        )
        await bob.request(FESL, "pres", status("chat", ("status.status", "Rush, Arica")))
        await ann.request(FESL, "pres", users("PresenceUnsubscribe", bob_id))
        assert ann.pushed(EVENT) is None


async def test_a_session_sent_away_while_its_persona_is_looked_up_leaves_the_persona_playing(
    server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The persona lookup waits until the account has logged in elsewhere, as a slow database
    # would let it.
    looking, release = asyncio.Event(), asyncio.Event()
    find = SqlPersonaStore.find

    async def slow_find(store: SqlPersonaStore, namespace: str, name: str) -> Persona | None:
        if name == "Ann" and not release.is_set():
            looking.set()
            await release.wait()
        return await find(store, namespace, name)

    async with game(server_url) as first, game(server_url) as second, game(server_url) as carl:
        await log_in_persona(carl, FESL, "carl@example.test", "Carl")
        await log_in_player(first, FESL, "ann@example.test")
        await first.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", "Ann")])
        monkeypatch.setattr(SqlPersonaStore, "find", slow_find)
        playing = format_fields([("TXN", "NuLoginPersona"), ("name", "Ann")])
        await first.websocket.send(encode_frame(Data(FESL, Packet("acct", 0xC0, 9, playing))))
        await looking.wait()
        await second.hello(FESL, "bfbc2-pc")
        await second.request(FESL, "acct", login("ann@example.test", "secret-pass"))
        release.set()

        ann_id = (await second.request(FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "Ann")]))[
            "userId"
        ]
        await second.request(FESL, "pres", status("chat"))
        await carl.request(FESL, "pres", users("PresenceSubscribe", int(ann_id)))
        assert (await carl.push(EVENT))["status.show"] == "chat"
        # Logging the first session in again ends whatever persona it still played as.
        await log_in_player(first, ConnectionKey(Channel.FESL, 2), "dee@example.test")
        await carl.request(FESL, "pres", users("PresenceSubscribe", int(ann_id)))
        assert (await carl.push(EVENT))["status.show"] == "chat"
