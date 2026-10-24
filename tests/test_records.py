import pytest
from support import Answer, game, log_in_persona

from openfesl.protocol.fields import Fields
from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)

FIRST_TAG = "QW5uIHRvb2sgdGhpcyB0YWcgaW4gcm91bmQgMQ=="
SECOND_TAG = "QW5uIHRvb2sgdGhpcyB0YWcgaW4gcm91bmQgMg=="


def record(txn: str, name: str, owner: int | None = None, owner_type: str = "1") -> Fields:
    # As the SDK writes it: the owner only when the caller names one.
    owned = [("owner", str(owner)), ("ownerType", owner_type)] if owner is not None else []
    return [("TXN", txn), *owned, ("recordName", name)]


def entries(*pairs: tuple[str, str]) -> Fields:
    fields: Fields = [("values.[]", str(len(pairs)))]
    for index, (key, value) in enumerate(pairs):
        fields += [(f"values.{index}.key", key), (f"values.{index}.value", value)]
    return [*fields, ("TTL", "0")]


def removals(*keys: str) -> Fields:
    return [("remove.[]", str(len(keys))), *((f"remove.{i}", key) for i, key in enumerate(keys))]


def listed(answer: Answer) -> list[tuple[str, str]]:
    return [
        (answer[f"values.{index}.key"], answer[f"values.{index}.value"])
        for index in range(int(answer["values.[]"]))
    ]


async def test_a_player_keeps_records_as_lists_and_maps_that_others_only_read(
    server_url: str,
) -> None:
    async with game(server_url) as ann_game, game(server_url) as bob_game:
        ann, _ = await log_in_persona(ann_game, FESL, "ann@example.test", "Ann_01")
        bob, _ = await log_in_persona(bob_game, FESL, "bob@example.test", "Bob_02")
        other = str(bob + 1000)

        for txn in ("GetRecord", "GetRecordAsMap"):
            missing = await ann_game.request(FESL, "recp", record(txn, "dogtags"))
            assert missing["errorCode"] == "5000"

        tags = entries((str(bob), FIRST_TAG), (other, SECOND_TAG))
        added = await ann_game.request(FESL, "recp", [*record("AddRecord", "dogtags"), *tags])
        assert "errorCode" not in added
        changes = [
            *record("UpdateRecord", "dogtags"),
            *entries((str(bob), SECOND_TAG), ("42", FIRST_TAG)),
            *removals(other),
        ]
        assert "errorCode" not in await ann_game.request(FESL, "recp", changes)
        own = await ann_game.request(FESL, "recp", record("GetRecord", "dogtags"))
        assert listed(own) == sorted([(str(bob), SECOND_TAG), ("42", FIRST_TAG)])

        clan = [*record("AddRecordAsMap", "clan"), ("values.{tag}", "ABCD"), ("TTL", "0")]
        assert "errorCode" not in await ann_game.request(FESL, "recp", clan)
        as_map = await ann_game.request(FESL, "recp", record("GetRecordAsMap", "clan"))
        assert (as_map["values.{tag}"], as_map["state"], as_map["TTL"]) == ("ABCD", "1", "0")
        assert as_map["lastModified"].endswith(" UTC")

        seen = await bob_game.request(FESL, "recp", record("GetRecord", "dogtags", ann))
        assert listed(seen) == listed(own)
        for refused in [
            [*record("UpdateRecord", "dogtags", ann), *entries((str(bob), FIRST_TAG))],
            [*record("AddRecord", "dogtags", bob, owner_type="11"), *entries(("1", "x"))],
            [*record("AddRecord", "notes"), *entries(("1", "x"))],
            [*record("AddRecord", "dogtags"), *entries(("a key", "x"))],
            [*record("AddRecord", "dogtags"), *entries(("1", "x" * 255))],
            [*record("AddRecordAsMap", "clan"), *((f"values.{{k{i}}}", "x") for i in range(17))],
        ]:
            assert (await bob_game.request(FESL, "recp", refused))["errorCode"] == "21"
        after = await bob_game.request(FESL, "recp", record("GetRecord", "dogtags", ann))
        assert listed(after) == listed(own)
        bobs_clan = await bob_game.request(FESL, "recp", record("GetRecord", "clan"))
        assert bobs_clan["errorCode"] == "5000"

        cleared = [*record("UpdateRecordAsMap", "clan"), ("TTL", "0"), *removals("tag")]
        assert "errorCode" not in await ann_game.request(FESL, "recp", cleared)
        gone = await ann_game.request(FESL, "recp", record("GetRecordAsMap", "clan"))
        assert gone["errorCode"] == "5000"

    async with game(server_url) as stranger:
        await stranger.hello(FESL, "bfbc2-pc")
        anonymous = await stranger.request(FESL, "recp", record("GetRecord", "dogtags", ann))
        assert anonymous["errorCode"] == "20"
