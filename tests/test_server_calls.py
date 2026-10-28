import logging
import secrets
from xml.etree import ElementTree

import anyio
import pytest
from heroes import MAGMA, create_hero, start
from support import THEATER, Game, TheaterAnswer, cli, fetch, game, let_in
from test_player_servers import SERVER_GAME, register, until_listed

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("prompt_decisions")]

PLAYER = "ann@example.test"
HOST = "host@example.test"


async def make_heroes(server_url: str, *heroes: tuple[str, str]) -> None:
    """Register accounts and create a hero of each, by login name and hero name."""
    base = server_url.replace("ws://", "http://").rstrip("/")
    for account, hero in heroes:
        await register(server_url, account)
        await create_hero(base, account, hero)


async def host(server_url: str, server: Game) -> tuple[TheaterAnswer, int]:
    """Log a dedicated server in as the host's hero and list its game; return it and the host."""
    _, host_account, _ = await start(server_url, server, HOST, role="host")
    return await server.theater(THEATER, "CGAM", SERVER_GAME), host_account


async def approve(
    capsys: pytest.CaptureFixture[str], server: Game, player: Game, created: TheaterAnswer
) -> None:
    """Approve a server's game and let a player in, whose data the server then keeps."""
    await cli(capsys, "servers", "approve", created.fields["UGID"])
    await until_listed(player)
    await let_in(server, player, created.fields)


async def ask(server_url: str, path: str, token: str | None, *, post: bool = False) -> bytes:
    """Send a dedicated server's Magma call as the hook forwards it, with a relay token if any."""
    headers = {"X-OpenFESL-Relay": token} if token else {}
    _, _, body = await fetch(server_url, path, MAGMA, headers, b"" if post else None)
    return body


def cause(body: bytes) -> str | None:
    """Return why a Magma call was refused, or None for an answer that is no refusal."""
    failure = ElementTree.fromstring(body).find("failure")
    return failure.get("cause") if failure is not None else None


async def test_a_dedicated_server_looks_up_its_players_hero_names_by_its_relay_token(
    server_url: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    # The WebSocket library logs handshakes at DEBUG, and the log settings that other tests'
    # Uvicorn servers load raise its level and keep Uvicorn's records from the root logger.
    caplog.set_level(logging.DEBUG, logger="uvicorn.error")
    monkeypatch.setattr(logging.getLogger("uvicorn"), "propagate", True)
    await make_heroes(server_url, (PLAYER, "Bramble"), (HOST, "Keeper"))
    async with game(server_url) as ann:
        _, ann_account, _ = await start(server_url, ann, PLAYER)
        async with game(server_url) as server:
            created, host_account = await host(server_url, server)
            token = server.relay_token
            assert token is not None and ann.relay_token is not None
            path = f"/nucleus/name/{ann_account}"
            unapproved = await ask(server_url, path, token)
            await approve(capsys, server, ann, created)

            named = await ask(server_url, path, token)
            refused = [
                await ask(server_url, path, None),
                await ask(server_url, path, secrets.token_urlsafe(32)),
                await ask(server_url, path, ann.relay_token),
                await ask(server_url, f"/nucleus/name/{host_account}", token),
            ]
        # Once the server's WebSocket closes, its token names nobody.
        with anyio.fail_after(5):
            while cause(closed := await ask(server_url, path, token)) != "UNKNOWN_SESSION":
                await anyio.sleep(0.02)

    # The hero's name, never the login name; each WebSocket has a token of its own.
    assert named == b"<name>Bramble</name>"
    assert len(token) == 43 and token != ann.relay_token
    assert cause(unapproved) == "NOT_A_SERVER"
    assert [cause(body) for body in refused] == [
        "NO_SESSION",
        "UNKNOWN_SESSION",
        "NOT_A_SERVER",
        "USER_DOES_NOT_MATCH",
    ]
    assert ElementTree.fromstring(closed).tag == "error"
    # The server's log, apart from the test's own clients'.
    logged = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.split(".")[0] in ("openfesl", "uvicorn")
    )
    assert "x-openfesl-relay-token: (masked)" in logged
    assert token not in logged and ann.relay_token not in logged
