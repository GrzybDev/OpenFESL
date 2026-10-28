import asyncio
import logging
import secrets
import uuid
from xml.etree import ElementTree

import anyio
import pytest
from accounts import select_value
from heroes import MAGMA, create_hero, start, wallets
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import THEATER, Game, TheaterAnswer, cli, fetch, game, let_in
from test_player_servers import SERVER_GAME, register, until_listed

from openfesl.core.wallets import Wallet
from openfesl.storage.wallets import credit

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


async def test_a_dedicated_server_credits_a_rounds_valor_once_within_its_cap(
    server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    await make_heroes(server_url, (PLAYER, "Bramble"), (HOST, "Keeper"))
    # Ann's second hero stays out of the server's game.
    briar = await create_hero(server_url.replace("ws://", "http://").rstrip("/"), PLAYER, "Briar")
    async with game(server_url) as ann, game(server_url) as server:
        cookie, account, hero = await start(server_url, ann, PLAYER)
        created, _ = await host(server_url, server)
        await approve(capsys, server, ann, created)
        token = server.relay_token

        async def send(code: str, amount: int, transaction: str, persona: int = hero) -> bytes:
            path = f"/nucleus/wallets/{persona}/{code}/{amount}/{transaction}"
            return await ask(server_url, path, token, post=True)

        first = str(uuid.uuid4())
        credited = await send("_DV", 250, first)
        repeated = await send("_DV", 250, first)
        # Two sends of one credit at once, as a retry might, credit it once.
        twice = await asyncio.gather(*(send("_DV", 100, "retried-round") for _ in range(2)))
        beyond_cap = await send("_DV", 900, str(uuid.uuid4()))
        # Hero Points come with levels, so a server's are recorded and credited 0.
        hero_points = await send("_DH", 3, str(uuid.uuid4()))
        refused = [
            await send("_PF", 100, str(uuid.uuid4())),
            await send("_DV", 0, str(uuid.uuid4())),
            await send("_DV", 2**31, str(uuid.uuid4())),
            await send("_DV", 5, "x" * 65),
            await send("_DV", 5, str(uuid.uuid4()), persona=briar),
        ]
        balances = await wallets(server_url, cookie, hero)

        # Whatever credits a balance, it never passes the game's 32-bit balance.
        engine = create_async_engine(database_url)
        funds = Wallet("bfheroes", "_PF", account, None, 2500)
        async with async_sessionmaker(engine).begin() as session:
            topped = [await credit(session, funds, amount, "reward") for amount in (2**31, 5)]
        await engine.dispose()
        topped_up = await wallets(server_url, cookie, hero)

    assert credited == b"<success />"
    assert repeated == (
        b'<error code="INTERNAL_ERROR"><failure cause="DUPLICATE_TRANSACTION_UUID" /></error>'
    )
    assert sorted(cause(body) or "" for body in twice) == ["", "DUPLICATE_TRANSACTION_UUID"]
    assert cause(beyond_cap) is None and cause(hero_points) is None
    assert [cause(body) for body in refused] == [*["INVALID_CREDIT"] * 4, "USER_DOES_NOT_MATCH"]
    assert balances == {"_DH": 0, "_DV": 1000, "_PF": 2500}
    ledger = await select_value(
        database_url,
        "SELECT string_agg(currency || ':' || reason || ':' || delta, ' ' ORDER BY wallet_entry.id)"
        " FROM wallet_entry JOIN wallet ON wallet.id = wallet_id"
        " WHERE account_id = :account AND currency <> '_PF'",
        account=account,
    )
    assert ledger == "_DV:server:250 _DV:server:100 _DV:server:650 _DH:server:0"
    assert topped == [2**31 - 1, 2**31 - 1]
    assert topped_up["_PF"] == 2**31 - 1
