import time
from collections.abc import Iterator
from xml.etree import ElementTree

import pytest
from accounts import execute, register, select_value
from heroes import MAGMA, create_hero, start
from support import FESL, THEATER, cli, fetch, game, let_in, png_image
from test_player_servers import SERVER_GAME, until_listed

pytestmark = pytest.mark.anyio

ANN = "ann@example.test"
BOB = "bob@example.test"
HOST = "host@example.test"
# The boundary that the game's uploads always carry.
BOUNDARY = "-----------4bda4b861f6"


def multipart(picture: bytes) -> bytes:
    """Return the body of a mugshot upload as the game writes it.

    Args:
        picture: The mugshot's file.
    """
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="mugshot.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    )
    return head.encode() + picture + f"\r\n--{BOUNDARY}--\r\n".encode()


def trickled(body: bytes) -> Iterator[bytes]:
    """Yield a body as a slow link delivers it, its rest only once the server could answer.

    Args:
        body: The whole body.
    """
    yield body[:1024]
    time.sleep(0.5)
    yield body[1024:]


async def upload(
    server_url: str, cookie: dict[str, str], hero: int, picture: bytes, slow: bool = False
) -> str:
    """Upload a hero's mugshot as the game does, returning the answer's root or failure cause.

    Args:
        server_url: The server's relay address.
        cookie: The uploading game's session cookie.
        hero: The hero whose mugshot it is.
        picture: The mugshot's file.
        slow: Whether the body arrives as over a slow link.
    """
    content = multipart(picture)
    headers = {
        **cookie,
        "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        "Content-Length": str(len(content)),
    }
    path = f"/api/mugshot/upload/{hero}/1"
    sent = trickled(content) if slow else content
    _, _, body = await fetch(server_url, path, MAGMA, headers, sent)
    answer = ElementTree.fromstring(body)
    failure = answer.find("failure")
    return failure.get("cause", "") if failure is not None else answer.tag


async def test_a_game_uploads_mugshots_of_its_own_heroes_only(
    base: str, server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as registering:
        await registering.hello(FESL, "bfbc2-pc")
        await register(registering, HOST)
    for address in (ANN, HOST):
        await create_hero(base, address, f"{address.split('@')[0]}_hero")
    bob_hero = await create_hero(base, BOB, "bob_hero")
    # 1.89 renders its heroes 1024 pixels square.
    first, second = png_image(64, 48), png_image(1024, 1024)
    async with game(server_url) as player, game(server_url) as server:
        ann, _, hero = await start(server_url, player, ANN)
        host, _, _ = await start(server_url, server, HOST, role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        await cli(capsys, "servers", "approve", created.fields["UGID"])
        await until_listed(player)
        await let_in(server, player, created.fields)
        allowed = [
            (await fetch(server_url, f"/api/mugshot/mayupload/{asked}", MAGMA, cookie))[2]
            for cookie, asked in ((ann, hero), (ann, bob_hero), ({}, hero), (host, hero))
        ]
        # The game uploads the same picture again and again, which keeps its version.
        uploads = [
            await upload(server_url, ann, hero, picture) for picture in (first, second, second)
        ]
        kept = await select_value(
            database_url, "SELECT version FROM mugshot WHERE persona_id = :hero", hero=hero
        )
        refused = [
            await upload(server_url, cookie, asked, picture)
            for cookie, asked, picture in (
                (ann, bob_hero, first),
                # A server keeps its players' data, yet only a player renders its hero.
                (host, hero, first),
                (ann, hero, b"GIF89a" + first),
                (ann, hero, png_image(1100, 20)),
            )
        ]
        refused.append(await upload(server_url, ann, hero, first + bytes(1_100_000), slow=True))
        image = await select_value(
            database_url, "SELECT image FROM mugshot WHERE persona_id = :hero", hero=hero
        )
        heroes = await select_value(database_url, "SELECT count(*) FROM mugshot")

    # The game reads a raw 1 as allowed, anything else as not.
    assert allowed == [b"1", b"0", b"0", b"0"]
    assert uploads == ["success"] * 3
    assert kept == 2
    assert refused == ["USER_DOES_NOT_MATCH", "USER_DOES_NOT_MATCH"] + ["NOT_A_MUGSHOT"] * 3
    # Nothing refused replaced the picture, nor gave another hero one.
    assert (image, heroes) == (second, 1)
    # The mugshot goes with its hero's account.
    await execute(database_url, "DELETE FROM account WHERE nuid = :ann", ann=ANN)
    assert await select_value(database_url, "SELECT count(*) FROM mugshot") == 0
