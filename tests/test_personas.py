import pytest
from support import game, log_in_player, login

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
