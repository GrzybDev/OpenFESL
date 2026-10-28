from xml.etree import ElementTree

import pytest
from accounts import register
from heroes import MAGMA, create_hero, start
from support import FESL, THEATER, cli, fetch, game, let_in
from test_player_servers import SERVER_GAME, until_listed

pytestmark = pytest.mark.anyio

ANN = "ann@example.test"
BOB = "bob@example.test"
HOST = "host@example.test"


async def answer(server_url: str, cookie: dict[str, str], path: str) -> tuple[str, str | None]:
    """Return the root of a Magma answer and the cause of its failure, if it is one.

    The checks are fetched, the profile refreshes posted with no body.
    """
    posted = b"" if path.startswith("/user/") else None
    _, _, body = await fetch(server_url, path, MAGMA, cookie, posted)
    root = ElementTree.fromstring(body)
    failure = root.find("failure")
    return root.tag, failure.get("cause") if failure is not None else None


async def test_a_game_passes_the_checks_of_its_own_account_and_heroes_and_a_server_of_its_players(
    base: str, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as registering:
        await registering.hello(FESL, "bfbc2-pc")
        await register(registering, HOST)
    # The game names accounts and heroes alike, and their numbers overlap, so Bob's hero gets a
    # number that no account has.
    for address, name in ((ANN, "ann_hero"), (HOST, "host_hero"), (ANN, "ann_two")):
        await create_hero(base, address, name)
    bob_hero = await create_hero(base, BOB, "bob_hero")
    async with game(server_url) as player, game(server_url) as server:
        ann, ann_account, ann_hero = await start(server_url, player, ANN)
        host, host_account, _ = await start(server_url, server, HOST, role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        await cli(capsys, "servers", "approve", created.fields["UGID"])
        await until_listed(player)
        await let_in(server, player, created.fields)
        asked_by_ann = [
            await answer(server_url, ann, path)
            for path in (
                f"/nucleus/check/user/{ann_account}",
                f"/nucleus/check/persona/{ann_hero}",
                f"/user/updateUserProfile/{ann_hero}",
                f"/user/updateUserProfile/{ann_account}",
                f"/nucleus/check/persona/{bob_hero}",
                f"/user/updateUserProfile/{bob_hero}",
                f"/nucleus/check/user/{host_account}",
            )
        ]
        # The server keeps the data of the players in its game, by hero and by account.
        asked_by_host = [
            await answer(server_url, host, path)
            for path in (
                f"/nucleus/check/persona/{ann_hero}",
                f"/user/updateUserProfile/{ann_hero}",
                f"/user/updateUserProfile/{ann_account}",
                f"/user/updateUserProfile/{bob_hero}",
            )
        ]
        anonymous = await answer(server_url, {}, f"/nucleus/check/user/{ann_account}")

    assert asked_by_ann == [
        ("success", None),
        ("success", None),
        ("success", None),
        ("success", None),
        ("error", "USER_DOES_NOT_MATCH"),
        ("error", "USER_DOES_NOT_MATCH"),
        ("error", "USER_DOES_NOT_MATCH"),
    ]
    assert asked_by_host == [
        ("success", None),
        ("success", None),
        ("success", None),
        ("error", "USER_DOES_NOT_MATCH"),
    ]
    assert anonymous == ("error", "NO_SESSION")
