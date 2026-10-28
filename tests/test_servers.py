import asyncio
import struct
import uuid
from pathlib import Path

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from support import (
    Game,
    TheaterAnswer,
    cli,
    connect_server,
    create,
    enter_as_player,
    game,
    masked,
    report,
)

from openfesl.protocol.relay import Channel, ConnectionKey
from openfesl.storage.servers import SqlServerStore

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)


async def shown(looker: Game, created: TheaterAnswer, attribute: str) -> list[str] | None:
    """Return the values of an attribute, in any spelling, that a lookup of a listed game shows.

    None while the game is not listed, as before the server reads its approval back.
    """
    ids = [("LID", created.fields["LID"]), ("GID", created.fields["GID"])]
    looked_up = await looker.theater(THEATER, "GDAT", ids)
    if looked_up.error:
        return None
    return [value for key, value in looked_up.fields.items() if key.casefold() == attribute]


async def until_marked(looker: Game, created: TheaterAnswer, mark: str) -> None:
    """Wait until a game shows the game's own server mark as the admin last decided."""
    with anyio.fail_after(5):
        while await shown(looker, created, "b-u-ea") != [mark]:
            await anyio.sleep(0.02)


async def games_seen(player: Game) -> int:
    await player.theater(THEATER, "LLST", [("FILTER-FAV-ONLY", "0")])
    return int((await player.theater_push(THEATER, "LDAT")).fields["NUM-GAMES"])


async def until_seen(player: Game, count: int) -> None:
    """Wait until the lobby shows a player some number of games, as the admin decided."""
    with anyio.fail_after(5):
        while await games_seen(player) != count:
            await anyio.sleep(0.02)


async def test_a_server_keeps_the_identity_it_is_issued_and_proves_it_with_its_secret(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as first, game(server_url) as twin:
        await connect_server(first)
        issued = await create(first)
        assert issued.error is None
        ugid, secret = issued.fields["UGID"], issued.fields["SECRET"]
        assert str(uuid.UUID(ugid)) == ugid
        # What the server's serverid.dat and every hop on the way carry unchanged.
        assert 0 < len(secret) <= 127 and secret.isascii() and secret.isprintable()
        assert not set(' ,%"=') & set(secret)
        await connect_server(twin)
        assert (await create(twin, ugid, secret)).error == "bsec"
        assert (await create(twin, ugid, "guessed")).error == "bsec"

    async with game(server_url) as restarted:
        await connect_server(restarted)
        # It tries again until the master has seen its first run end, as R34 does.
        with anyio.fail_after(5):
            while (kept := await create(restarted, ugid, secret)).error == "bsec":
                await anyio.sleep(0.02)
        assert (kept.error, kept.fields["UGID"], kept.fields["SECRET"]) == (None, ugid, secret)

    # Ids the master did not issue, or that no file could hold, are no identity.
    async with game(server_url) as stranger:
        await connect_server(stranger)
        for claimed in (str(uuid.uuid4()), "a\0b"):
            fresh = await create(stranger, claimed, secret)
            assert fresh.error is None
            assert fresh.fields["UGID"] not in {ugid, claimed}
            assert (await stranger.theater(THEATER, "RGAM", [("TYPE", "G")])).error is None
        # The name only tells the admin which server it is; what text cannot hold goes.
        assert (await create(stranger, name="Invented\0Server")).error is None
    listed = (await cli(capsys, "servers", "list")).splitlines()
    assert (listed[0], len(listed)) == (f"{ugid}  pending  -  Invented Server #1", 4)
    assert listed[-1].endswith("  pending  -  InventedServer")


async def test_players_see_a_new_server_once_approved_and_lose_it_when_revoked(
    prompt_decisions: None, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli(capsys, "hosting") == "approved\n"
    async with game(server_url) as server, game(server_url) as ann:
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await connect_server(server)
        created = await create(server)
        assert created.error is None
        ugid, secret = created.fields["UGID"], created.fields["SECRET"]
        assert await games_seen(ann) == 0
        listed = await ann.theater(THEATER, "GLST", [("LID", "1"), ("TYPE", "G")])
        assert (listed.fields["NUM-GAMES"], listed.fields["LOBBY-NUM-GAMES"]) == ("0", "0")
        ids = [("LID", created.fields["LID"]), ("GID", created.fields["GID"])]
        assert (await ann.theater(THEATER, "GDAT", ids)).error == "ngam"
        join = [*ids, ("PORT", "50111")]
        assert (await ann.theater(THEATER, "EGAM", join)).error == "ngam"

        await cli(capsys, "servers", "approve", ugid)
        await until_seen(ann, 1)
        assert (await ann.theater(THEATER, "GDAT", ids)).fields["N"] == "Invented Server #1"
        assert (await ann.theater(THEATER, "EGAM", join)).error is None
        await cli(capsys, "servers", "revoke", ugid)
        removed = await ann.theater_push(THEATER, "GREM")
        assert [(key, removed.fields[key]) for key in ("LID", "GID")] == ids
        assert await games_seen(ann) == 0
        # The host keeps its connection, since a dedicated server drops its players with it and
        # quits after losing it often; it hears that its game ended, and asks again in vain.
        ended = await server.theater_push(THEATER, "GREM")
        assert [(key, ended.fields[key]) for key in ("LID", "GID")] == ids
        assert (await create(server, ugid, secret)).error == "auth"
        assert await cli(capsys, "servers", "list") == f"{ugid}  revoked  -  Invented Server #1\n"

    async with game(server_url) as server:
        await connect_server(server)
        assert (await create(server, ugid, secret)).error == "auth"
    with pytest.raises(SystemExit, match="No server has the id"):
        await cli(capsys, "servers", "approve", str(uuid.uuid4()))


async def test_servers_report_the_provider_whose_file_they_hold(
    prompt_decisions: None, server_url: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "ProviderId.dat"
    added = (await cli(capsys, "providers", "add", "Invented Hosting", "--out", str(out))).strip()
    file = out.read_bytes()
    assert (len(file), file[:16].hex().upper()) == (20, added)
    assert sum(struct.unpack("<5I", file)) % 2**32 == 0xC18E65EF
    with pytest.raises(SystemExit, match="exists"):
        await cli(capsys, "providers", "add", "Other Hosting", "--out", str(out))
    assert out.read_bytes() == file

    async with game(server_url) as server:
        await connect_server(server)
        created = await create(server)
        ugid = created.fields["UGID"]
        listed = f"{ugid}  pending  Invented Hosting  Invented Server #1\n"
        # A server masks the id afresh on every start; the decoded id stays the same.
        for mask in (bytes(range(1, 9)), bytes.fromhex("FEDCBA9876543210")):
            await report(server, created, ("B-U-Provider", masked(file[:16], mask)))
            assert await cli(capsys, "servers", "list") == listed
        # Anything but a masked id is no report at all.
        await report(server, created, ("b-u-PROVIDER", "forged"))
        assert await cli(capsys, "servers", "list") == listed
        # Game lists show the provider's name, which no host can claim for its game.
        await cli(capsys, "servers", "approve", ugid)
        await until_seen(server, 1)
        await report(server, created, ("B-U-Provider", "forged"))
        assert await shown(server, created, "b-u-provider") == ["Invented Hosting"]

        # Revoking the provider revokes its servers, also those that report it later, and
        # takes their official marks.
        await cli(capsys, "servers", "official", ugid)
        assert "  official  Invented Hosting  " in await cli(capsys, "servers", "list")
        await cli(capsys, "providers", "revoke", added)
        await server.theater_push(THEATER, "GREM")
    assert await cli(capsys, "providers", "list") == f"{added}  revoked  Invented Hosting\n"
    assert "  revoked  Invented Hosting  " in await cli(capsys, "servers", "list")
    with pytest.raises(SystemExit, match="is not approved"):
        await cli(capsys, "servers", "official", ugid)
    async with game(server_url) as newcomer:
        await connect_server(newcomer)
        created = await create(newcomer)
        assert created.error is None
        ids = [("LID", created.fields["LID"]), ("GID", created.fields["GID"])]
        provider = ("B-U-Provider", masked(file[:16], bytes(8)))
        await newcomer.send_theater(THEATER, "UGAM", [*ids, provider, ("TID", "0")])
        await newcomer.theater_push(THEATER, "GREM")
    with pytest.raises(SystemExit, match="No provider has the id"):
        await cli(capsys, "providers", "revoke", "not-an-id")


async def until_waiting(engine: AsyncEngine, count: int) -> None:
    """Wait until some number of the database's sessions wait for a lock."""
    waiting = text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")
    with anyio.fail_after(5):
        while True:
            # A connection of its own each time, as a transaction reads the activity only once.
            async with engine.connect() as connection:
                if await connection.scalar(waiting) >= count:
                    return
            await anyio.sleep(0.02)


async def test_a_mark_set_while_its_provider_is_revoked_does_not_outlast_the_revocation(
    server_url: str, database_url: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    out = tmp_path / "ProviderId.dat"
    await cli(capsys, "providers", "add", "Invented Hosting", "--out", str(out))
    provider_id = out.read_bytes()[:16]
    async with game(server_url) as server:
        await connect_server(server)
        created = await create(server)
        ugid = created.fields["UGID"]
        await report(server, created, ("B-U-Provider", masked(provider_id, bytes(8))))
        await cli(capsys, "servers", "approve", ugid)
    engine = create_async_engine(database_url)
    store = SqlServerStore(async_sessionmaker(engine))
    async with engine.connect() as holding:
        # The server's row stays locked, so the revocation halts with its provider revoked.
        await holding.execute(
            text("SELECT ugid FROM game_server WHERE ugid = :ugid FOR UPDATE"), {"ugid": ugid}
        )
        revoking = asyncio.create_task(store.revoke_provider(provider_id, None))
        await until_waiting(engine, 1)
        marking = asyncio.create_task(store.set_official(ugid, True, None))
        await until_waiting(engine, 2)
    assert (await revoking, await marking) == (True, False)
    assert not (await store.find([ugid]))[0].official_mark
    await engine.dispose()


async def test_open_hosting_lists_every_server_at_once_and_still_records_it(
    prompt_decisions: None, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli(capsys, "hosting", "open") == "open\n"
    async with game(server_url) as server, game(server_url) as ann:
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await connect_server(server)
        created = await create(server)
        assert created.error is None
        assert await games_seen(ann) == 1
        unknown = bytes(range(16))
        await report(server, created, ("B-U-Provider", masked(unknown, bytes(8))))
        ugid = created.fields["UGID"]
        recorded = f"{ugid}  pending  {unknown.hex().upper()}  Invented Server #1\n"
        assert await cli(capsys, "servers", "list") == recorded
        await report(server, created, ("B-U-PROVIDER", "forged"))
        assert await shown(ann, created, "b-u-provider") == []

        # Closing hosting again hides the servers that wait for approval.
        assert await cli(capsys, "hosting", "approved") == "approved\n"
        await until_seen(ann, 0)
        await cli(capsys, "hosting", "open")
        await until_seen(ann, 1)


async def test_only_official_servers_show_the_games_own_mark_until_unmarked_or_revoked(
    prompt_decisions: None, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as kept, game(server_url) as other, game(server_url) as newcomer:
        await connect_server(kept)
        kept_game = await create(kept, name="Invented Official Rush")
        await connect_server(other)
        other_game = await create(other, name="Invented Other Rush")
        kept_ugid, secret = kept_game.fields["UGID"], kept_game.fields["SECRET"]
        for created in (kept_game, other_game):
            await cli(capsys, "servers", "approve", created.fields["UGID"])
        await until_seen(kept, 2)
        await until_marked(kept, kept_game, "0")

        # Only the admin marks a server; no host sets the game's mark for its own game.
        await report(other, other_game, ("b-u-EA", "1"))
        await cli(capsys, "servers", "official", kept_ugid)
        await until_marked(kept, kept_game, "1")
        assert await shown(kept, other_game, "b-u-ea") == ["0"]
        listed = await cli(capsys, "servers", "list")
        assert f"{kept_ugid}  official  -  Invented Official Rush\n" in listed
        assert "  approved  -  Invented Other Rush" in listed
        await report(kept, kept_game, ("B-U-EA", "0"))
        assert await shown(kept, kept_game, "b-u-ea") == ["1"]
        await cli(capsys, "servers", "unofficial", kept_ugid)
        await until_marked(kept, kept_game, "0")
        await cli(capsys, "servers", "official", kept_ugid)
        await until_marked(kept, kept_game, "1")

        # Revoking a server takes its mark for good: approving it again does not return it.
        await cli(capsys, "servers", "revoke", kept_ugid)
        await kept.theater_push(THEATER, "GREM")
        await cli(capsys, "servers", "approve", kept_ugid)
        assert f"{kept_ugid}  approved  -  " in await cli(capsys, "servers", "list")
        again = await create(kept, kept_ugid, secret)
        assert again.error is None
        await until_seen(other, 2)
        assert await shown(other, again, "b-u-ea") == ["0"]

        # Only approved servers take the mark.
        await connect_server(newcomer)
        waiting = (await create(newcomer, name="Invented Waiting Rush")).fields["UGID"]
        with pytest.raises(SystemExit, match=f"The server {waiting} is not approved"):
            await cli(capsys, "servers", "official", waiting)
    with pytest.raises(SystemExit, match="No server has the id"):
        await cli(capsys, "servers", "official", str(uuid.uuid4()))
