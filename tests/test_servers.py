import struct
import uuid
from pathlib import Path

import anyio
import pytest
from support import (
    CREATE_GAME,
    Game,
    TheaterAnswer,
    cli,
    enter_as_player,
    enter_theater,
    game,
    log_in_server,
)

from openfesl.core import hosting
from openfesl.protocol.relay import Channel, Close, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)


@pytest.fixture
def prompt_decisions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The admin decides in another process; the server reads the decisions back this often.
    monkeypatch.setattr(hosting, "APPROVAL_CHECK_INTERVAL", 0.05)


async def connect(server: Game) -> None:
    await enter_theater(server, THEATER, "bfbc2-pc-server", await log_in_server(server, FESL))


async def create(
    server: Game, ugid: str = "", secret: str = "", name: str = "Invented Server #1"
) -> TheaterAnswer:
    """List the server's game with the identity it keeps in its serverid.dat, if any."""
    sent = {**dict(CREATE_GAME), "UGID": ugid, "SECRET": secret, "NAME": name}
    return await server.theater(THEATER, "CGAM", list(sent.items()))


async def report(server: Game, created: TheaterAnswer, *attributes: tuple[str, str]) -> None:
    """Send a UGAM, then wait for the answer to a request behind it on the same connection."""
    ids = [("LID", created.fields["LID"]), ("GID", created.fields["GID"])]
    await server.send_theater(THEATER, "UGAM", [*ids, *attributes, ("TID", "0")])
    assert (await server.theater(THEATER, "UBRA", [*ids, ("START", "0")])).error is None


def masked(provider_id: bytes, mask: bytes) -> str:
    """Return a provider attribute as a server sends it: the id XORed with a mask, the mask."""
    data = bytes(byte ^ mask[index % 8] for index, byte in enumerate(provider_id)) + mask
    return data.hex().upper()


async def providers_shown(looker: Game, created: TheaterAnswer) -> list[str]:
    """Return the provider attributes that a lookup of a listed game shows."""
    ids = [("LID", created.fields["LID"]), ("GID", created.fields["GID"])]
    looked_up = await looker.theater(THEATER, "GDAT", ids)
    assert looked_up.error is None
    return [value for key, value in looked_up.fields.items() if key.casefold() == "b-u-provider"]


async def games_seen(player: Game) -> int:
    await player.theater(THEATER, "LLST", [("FILTER-FAV-ONLY", "0")])
    return int((await player.theater_push(THEATER, "LDAT")).fields["NUM-GAMES"])


async def until_seen(player: Game, count: int) -> None:
    """Wait until the lobby shows a player some number of games, as the admin decided."""
    with anyio.fail_after(5):
        while await games_seen(player) != count:
            await anyio.sleep(0.02)


async def closed(server: Game, key: ConnectionKey) -> None:
    """Wait until the server closes one of the game's connections."""
    with anyio.fail_after(5):
        while not [
            frame for frame in server.frames if isinstance(frame, Close) and frame.connection == key
        ]:
            server.frames.append(await server.next_frame())


async def test_a_server_keeps_the_identity_it_is_issued_and_proves_it_with_its_secret(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as first, game(server_url) as twin:
        await connect(first)
        issued = await create(first)
        assert issued.error is None
        ugid, secret = issued.fields["UGID"], issued.fields["SECRET"]
        assert str(uuid.UUID(ugid)) == ugid
        # What the server's serverid.dat and every hop on the way carry unchanged.
        assert 0 < len(secret) <= 127 and secret.isascii() and secret.isprintable()
        assert not set(' ,%"=') & set(secret)
        await connect(twin)
        assert (await create(twin, ugid, secret)).error == "bsec"
        assert (await create(twin, ugid, "guessed")).error == "bsec"

    async with game(server_url) as restarted:
        await connect(restarted)
        # It tries again until the master has seen its first run end, as R34 does.
        with anyio.fail_after(5):
            while (kept := await create(restarted, ugid, secret)).error == "bsec":
                await anyio.sleep(0.02)
        assert (kept.error, kept.fields["UGID"], kept.fields["SECRET"]) == (None, ugid, secret)

    # Ids the master did not issue, or that no file could hold, are no identity.
    async with game(server_url) as stranger:
        await connect(stranger)
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
        await connect(server)
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
        # The host learns from its closed Theater connection that it no longer hosts.
        await closed(server, THEATER)
        assert await cli(capsys, "servers", "list") == f"{ugid}  revoked  -  Invented Server #1\n"

    async with game(server_url) as server:
        await connect(server)
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
        await connect(server)
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
        assert await providers_shown(server, created) == ["Invented Hosting"]

        # Revoking the provider revokes its servers, also those that report it later.
        await cli(capsys, "providers", "revoke", added)
        await closed(server, THEATER)
    assert await cli(capsys, "providers", "list") == f"{added}  revoked  Invented Hosting\n"
    async with game(server_url) as newcomer:
        await connect(newcomer)
        created = await create(newcomer)
        assert created.error is None
        ids = [("LID", created.fields["LID"]), ("GID", created.fields["GID"])]
        provider = ("B-U-Provider", masked(file[:16], bytes(8)))
        await newcomer.send_theater(THEATER, "UGAM", [*ids, provider, ("TID", "0")])
        await closed(newcomer, THEATER)
    with pytest.raises(SystemExit, match="No provider has the id"):
        await cli(capsys, "providers", "revoke", "not-an-id")


async def test_open_hosting_lists_every_server_at_once_and_still_records_it(
    prompt_decisions: None, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await cli(capsys, "hosting", "open") == "open\n"
    async with game(server_url) as server, game(server_url) as ann:
        await enter_as_player(ann, "ann@example.test", "Ann_01")
        await connect(server)
        created = await create(server)
        assert created.error is None
        assert await games_seen(ann) == 1
        unknown = bytes(range(16))
        await report(server, created, ("B-U-Provider", masked(unknown, bytes(8))))
        ugid = created.fields["UGID"]
        recorded = f"{ugid}  pending  {unknown.hex().upper()}  Invented Server #1\n"
        assert await cli(capsys, "servers", "list") == recorded
        await report(server, created, ("B-U-PROVIDER", "forged"))
        assert await providers_shown(ann, created) == []

        # Closing hosting again hides the servers that wait for approval.
        assert await cli(capsys, "hosting", "approved") == "approved\n"
        await until_seen(ann, 0)
        await cli(capsys, "hosting", "open")
        await until_seen(ann, 1)
