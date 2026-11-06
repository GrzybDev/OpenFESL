from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from xml.etree import ElementTree

import pytest
from accounts import register
from heroes import MAGMA, start
from social import ANN, Player, entry, name_accounts, post, refusal, roster
from support import FESL, THEATER, cli, fetch, game
from test_player_servers import SERVER_GAME

pytestmark = pytest.mark.anyio

HOST = "host@example.test"
# What a 1.89 dedicated server reports: whether it is online, and its name.
ONLINE = (
    b'<update>\n\t<status><![CDATA[{"O":1}]]></status>\n'
    b"\t<name><![CDATA[Invented Heroes Server]]></name>\n</update>"
)


@asynccontextmanager
async def hosting(
    base: str, server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> AsyncIterator[tuple[Player, Player, str]]:
    """Start Ann's game and a dedicated server that lists an approved game.

    Yields:
        Ann's game and the server's, each with its magma cookie and account number, and the
        persistent id of the server's game.
    """
    async with game(server_url) as registering:
        await registering.hello(FESL, "bfbc2-pc")
        await register(registering, HOST)
    await name_accounts(base, database_url, ANN, HOST)
    async with game(server_url) as player, game(server_url) as server:
        ann_cookie, ann_id, _ = await start(server_url, player, ANN)
        host_cookie, host_id, _ = await start(server_url, server, HOST, role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        await cli(capsys, "servers", "approve", created.fields["UGID"])
        yield (ann_cookie, ann_id), (host_cookie, host_id), created.fields["UGID"]


async def server_roster(server_url: str, host: Player, path: str) -> list[dict[str, str]]:
    """Return the relationships a server's roster request lists, each child by name."""
    _, _, body = await fetch(server_url, f"/relationships/roster/{path}", MAGMA, host[0])
    answer = ElementTree.fromstring(body)
    assert answer.tag == "roster"
    return [entry(relationship) for relationship in answer]


def state(answer: ElementTree.Element) -> str:
    """Return the state of the relationship that a change answers."""
    assert answer.tag == "success"
    return entry(answer[0])["state"]


async def test_a_vip_sees_the_server_on_its_roster_and_its_revocation_until_it_leaves(
    base: str, server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with hosting(base, server_url, database_url, capsys) as (ann, host, ugid):
        server = f"server:{ugid}"
        invited = await post(
            server_url, host, f"/relationships/increase/{server}/nucleus:{ann[1]}/bvip"
        )
        [invitation] = await roster(server_url, ann)
        reported = await post(server_url, host, f"/relationships/status/{server}", ONLINE)
        accepted = await post(
            server_url, ann, f"/relationships/increase/nucleus:{ann[1]}/{server}/bvip"
        )
        [vip_server] = await roster(server_url, ann)
        vips = await server_roster(server_url, host, f"{server}/bvip/1,3")
        revoked = await post(
            server_url, host, f"/relationships/decrease/{server}/nucleus:{ann[1]}/bvip"
        )
        [revocation] = await roster(server_url, ann)
        after_revoking = await server_roster(server_url, host, f"{server}/bvip/1,3")
        left = await post(
            server_url, ann, f"/relationships/decrease/nucleus:{ann[1]}/{server}/bvip"
        )
        rosters = [await roster(server_url, ann), await server_roster(server_url, host, server)]

    # An invitation: the server agreed, the player has not yet.
    assert (state(invited), reported.tag) == ("1", "success")
    assert {key: invitation[key] for key in ("new", "state", "realid", "type", "name")} == {
        "new": "true",
        "state": "2",
        "realid": ugid,
        "type": "server",
        "name": "Invented Heroes Server",
    }
    # Once both agreed, the player's game reads the server's status line.
    assert (state(accepted), vip_server["state"], vip_server["status"]) == ("3", "3", '{"O":1}')
    assert [(vip["realid"], vip["type"], vip["name"], vip["state"]) for vip in vips] == [
        (str(ann[1]), "nucleus", "Invented_Ann", "3")
    ]
    # A server that takes a player off its list leaves the player's side, which its game reads
    # as its VIP status revoked; the player leaving ends it.
    assert (state(revoked), revocation["state"], revocation["new"]) == ("2", "1", "true")
    assert after_revoking == []
    assert state(left) == "0"
    assert rosters == [[], []]


async def test_a_server_acknowledges_a_request_and_acts_only_under_its_own_names(
    base: str, server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with hosting(base, server_url, database_url, capsys) as (ann, host, ugid):
        server = f"server:{ugid}"
        asked = await post(
            server_url, ann, f"/relationships/increase/nucleus:{ann[1]}/{server}/bvip"
        )
        [request] = await server_roster(server_url, host, server)
        waiting = await server_roster(server_url, host, f"{server}/bvip/1,3")
        seen = await post(server_url, host, f"/relationships/acknowledge/{server}/{request['id']}")
        [acknowledged] = await server_roster(server_url, host, server)
        accepted = await post(
            server_url, host, f"/relationships/increase/{server}/nucleus:{ann[1]}/bvip"
        )
        # It may go by its persona's name too, whose relationships are its own.
        by_hero = await server_roster(server_url, host, "server:bfh.server")
        refusals = [
            refusal(await post(server_url, player, path))
            for player, path in (
                (ann, f"/relationships/increase/{server}/nucleus:{ann[1]}/bvip"),
                (host, f"/relationships/increase/server:other/nucleus:{ann[1]}/bvip"),
                (ann, f"/relationships/increase/nucleus:{ann[1]}/server:other/bvip"),
                (host, f"/relationships/increase/{server}/nucleus:{ann[1]}/friends"),
                (ann, f"/relationships/increase/nucleus:{ann[1]}/nucleus:{host[1]}/bvip"),
                (ann, f"/relationships/increase/nucleus:{ann[1]}/player:{host[1]}/friends"),
            )
        ]

    assert state(asked) == "1"
    assert (request["realid"], request["state"], request["new"]) == (str(ann[1]), "2", "true")
    assert waiting == []
    assert (seen.tag, acknowledged["new"]) == ("success", "false")
    assert state(accepted) == "3"
    assert by_hero == []
    assert refusals == [
        ("VALIDATION_FAILED", "NOT_A_SERVER"),
        ("VALIDATION_FAILED", "USER_DOES_NOT_MATCH"),
        ("VALIDATION_FAILED", "SERVER_NOT_FOUND"),
        ("VALIDATION_FAILED", "UNKNOWN_TAG"),
        ("VALIDATION_FAILED", "UNKNOWN_TAG"),
        ("VALIDATION_FAILED", "INVALID_ENTITY"),
    ]
