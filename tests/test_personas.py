import pytest
from support import cli, game, log_in_player, login

from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)


def persona(txn: str, name: str) -> list[tuple[str, str]]:
    return [("TXN", txn), ("name", name)]


async def test_a_player_creates_plays_as_and_disables_personas(server_url: str) -> None:
    async with game(server_url) as player, game(server_url) as rival:
        await player.hello(FESL, "bfbc2-pc")
        early = await player.request(FESL, "acct", [("TXN", "NuGetPersonas"), ("namespace", "")])
        assert early["errorCode"] == "20"
        await rival.hello(FESL, "bfbc2-pc")
        await log_in_player(player, ConnectionKey(Channel.FESL, 2), "ann@example.test")
        await log_in_player(rival, ConnectionKey(Channel.FESL, 2), "eve@example.test")
        mine = ConnectionKey(Channel.FESL, 2)

        assert "errorCode" not in await player.request(
            mine, "acct", persona("NuAddPersona", "Ann_01")
        )
        taken = await rival.request(mine, "acct", persona("NuAddPersona", "ann_01"))
        assert taken["errorCode"] == "160"
        wanted = [("TXN", "NuSuggestPersonas"), ("name", "Ann_01"), ("maxSuggestions", "3")]
        suggested = await rival.request(mine, "acct", wanted)
        assert [suggested["names.0"], suggested["names.1"], suggested["names.2"]] == [
            "Ann_011",
            "Ann_012",
            "Ann_013",
        ]
        invalid = await player.request(mine, "acct", persona("NuAddPersona", "no spaces"))
        assert invalid["errorCode"] == "21"
        assert "errorCode" not in await player.request(
            mine, "acct", persona("NuAddPersona", "Ann.02")
        )

        listed = await player.request(mine, "acct", [("TXN", "NuGetPersonas"), ("namespace", "")])
        assert [listed["personas.[]"], listed["personas.0"], listed["personas.1"]] == [
            "2",
            "Ann_01",
            "Ann.02",
        ]
        stolen = await rival.request(mine, "acct", persona("NuLoginPersona", "Ann_01"))
        assert stolen["errorCode"] == "101"
        playing = await player.request(mine, "acct", persona("NuLoginPersona", "ann_01"))
        assert playing["lkey"].endswith(".")
        assert int(playing["userId"]) > 0

        assert "errorCode" not in await player.request(
            mine, "acct", persona("NuDisablePersona", "Ann.02")
        )
        listed = await player.request(mine, "acct", [("TXN", "NuGetPersonas"), ("namespace", "")])
        assert (listed["personas.[]"], listed["personas.0"]) == ("1", "Ann_01")
        again = await rival.request(mine, "acct", persona("NuAddPersona", "Ann.02"))
        assert again["errorCode"] == "160"
        # A disabled persona leaves room for another; the game has a notice for a full account.
        for number in range(2, 9):
            added = await player.request(mine, "acct", persona("NuAddPersona", f"Ann_0{number}"))
            assert "errorCode" not in added
        full = await player.request(mine, "acct", persona("NuAddPersona", "Ann_09"))
        assert full["errorCode"] == "163"


async def test_a_dedicated_server_creates_its_persona_once_and_looks_up_joining_players(
    server_url: str,
) -> None:
    async with game(server_url) as player, game(server_url) as first, game(server_url) as second:
        account = await log_in_player(player, FESL, "ann@example.test")
        await player.request(FESL, "acct", persona("NuAddPersona", "Ann_01"))

        squat = await player.request(FESL, "acct", persona("NuAddPersona", "bfbc2.server.p"))
        assert squat["errorCode"] == "160"
        for server in (first, second):
            await server.hello(FESL, "bfbc2-pc-server", client_type="server")
            await server.request(FESL, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
            listed = await server.request(
                FESL, "acct", [("TXN", "NuGetPersonas"), ("namespace", "")]
            )
            assert (listed["personas.[]"], listed["personas.0"]) == ("1", "bfbc2.server.p")
            playing = await server.request(
                FESL, "acct", persona("NuLoginPersona", "bfbc2.server.p")
            )
            assert "lkey" in playing
        for txn in ("NuAddPersona", "NuDisablePersona"):
            refused = await first.request(FESL, "acct", persona(txn, "bfbc2.server.p"))
            assert refused["errorCode"] == "21"

        lookup = await first.request(
            FESL,
            "acct",
            [
                ("TXN", "NuLookupUserInfo"),
                ("userInfo.0.userName", "ann_01"),
                ("userInfo.1.userName", "Nobody"),
                ("userInfo.2.userId", str(2**64)),
                ("userInfo.[]", "3"),
            ],
        )
        assert lookup["userInfo.[]"] == "1"
        assert lookup["userInfo.0.userName"] == "Ann_01"
        assert lookup["userInfo.0.masterUserId"] == account["userId"]
        assert lookup["userInfo.0.namespace"] == "battlefield"


def search(name: str, chunk_size: int = 0) -> list[tuple[str, str]]:
    fields = [("TXN", "NuSearchOwners"), ("screenName", name), ("searchType", "1")]
    fields.append(("retrieveUserIds", "1"))
    return fields + ([("chunkSize", str(chunk_size))] if chunk_size else [])


async def test_players_find_the_personas_of_others_by_name_with_wildcards(
    server_url: str,
) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        await log_in_player(ann, FESL, "ann@example.test")
        for name in ("Ann_01", "AnnX01", "Anna", "Ann.02"):
            await ann.request(FESL, "acct", persona("NuAddPersona", name))
        await ann.request(FESL, "acct", persona("NuDisablePersona", "Ann.02"))
        await log_in_player(bob, FESL, "bob@example.test")

        found = await bob.request(FESL, "acct", search("ann*"))
        names = {found[f"users.{index}.name"] for index in range(int(found["users.[]"]))}
        assert names == {"Ann_01", "AnnX01", "Anna"}
        assert found["users.0.type"] == "1"
        assert int(found["users.0.id"]) > 0
        exact = await bob.request(FESL, "acct", search("ann_01"))
        assert (exact["users.[]"], exact["users.0.name"]) == ("1", "Ann_01")
        # Only * is a wildcard, and the dedicated servers' persona is no player's.
        for literal in ("Ann%", "bfbc2*"):
            assert (await bob.request(FESL, "acct", search(literal)))["users.[]"] == "0"
        few = await bob.request(FESL, "acct", search("*", chunk_size=2))
        assert few["users.[]"] == "2"


async def test_the_admin_disables_a_persona_which_keeps_its_name_from_play(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as ann, game(server_url) as bob:
        await log_in_player(ann, FESL, "ann@example.test")
        await log_in_player(bob, FESL, "bob@example.test")
        for name in ("Ann_01", "Ann_02"):
            await ann.request(FESL, "acct", persona("NuAddPersona", name))

        await cli(capsys, "personas", "state", "ann_01", "disabled")
        # Still listed, so its player picks it and the game says why it cannot play.
        listed = await ann.request(FESL, "acct", [("TXN", "NuGetPersonas"), ("namespace", "")])
        assert (listed["personas.[]"], listed["personas.0"]) == ("2", "Ann_01")
        refused = await ann.request(FESL, "acct", persona("NuLoginPersona", "Ann_01"))
        assert refused["errorCode"] == "164"
        taken = await bob.request(FESL, "acct", persona("NuAddPersona", "ANN_01"))
        assert taken["errorCode"] == "164"
        assert "lkey" in await ann.request(FESL, "acct", persona("NuLoginPersona", "Ann_02"))

        # A name its player gave up stays taken, and the admin can keep it from play as well.
        await ann.request(FESL, "acct", persona("NuDisablePersona", "Ann_02"))
        given_up = persona("NuAddPersona", "Ann_02")
        assert (await bob.request(FESL, "acct", given_up))["errorCode"] == "160"
        await cli(capsys, "personas", "state", "Ann_02", "disabled")
        assert (await bob.request(FESL, "acct", given_up))["errorCode"] == "164"

        await cli(capsys, "personas", "state", "Ann_01", "active")
        assert "lkey" in await ann.request(FESL, "acct", persona("NuLoginPersona", "Ann_01"))

    for name in ("Nobody", "bfbc2.server.p"):
        with pytest.raises(SystemExit, match="No player's persona"):
            await cli(capsys, "personas", "state", name, "disabled")
