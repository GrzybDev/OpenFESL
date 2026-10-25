from pathlib import Path

import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import cli, connect_server, create, game, masked, report

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-2", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def test_the_home_lists_servers_that_wait_and_approves_them(
    admin_provider: IdentityProvider,
    server_url: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    out = tmp_path / "ProviderId.dat"
    await cli(capsys, "providers", "add", "Invented Hosting", "--out", str(out))
    known_id = out.read_bytes()[:16]
    async with (
        httpx2.AsyncClient() as client,
        game(server_url) as known,
        game(server_url) as stranger,
    ):
        home = await sign_in(client, base)
        assert "Nothing needs attention." in home.text

        await connect_server(known)
        known_game = await create(known, name="Invented Known Rush")
        rush = [("B-U-level", "Levels/MP_006"), ("B-U-gamemode", "RUSH")]
        await report(known, known_game, ("B-U-Provider", masked(known_id, bytes(8))), *rush)
        await connect_server(stranger)
        stranger_game = await create(stranger, name="Invented Stranger")
        unknown = bytes.fromhex("9E4107C25B88D3F01A6C44E90B72C815")
        await report(stranger, stranger_game, ("B-U-Provider", masked(unknown, bytes(8))))

        home = await client.get(f"{base}/admin/")
        page = home.text
        assert "Invented Known Rush" in page and "Invented Hosting" in page
        assert "Arica Harbor" in page and "Rush" in page
        assert "Invented Stranger" in page and "9E4107C2 5B88D3F0 1A6C44E9 0B72C815" in page
        assert "Servers waiting: 2" in page

        # A server of a known provider is approved at once, and the next page says so.
        known_ugid = known_game.fields["UGID"]
        approved = await client.post(
            f"{base}/admin/servers/{known_ugid}/approve",
            data={"csrf": form_token(page), "back": "/admin/"},
        )
        assert approved.headers["location"] == "/admin/"
        home = await client.get(f"{base}/admin/")
        assert "Approved Invented Known Rush." in home.text
        assert "Servers waiting: 1" in home.text
        assert "Approved Invented Known Rush." not in (await client.get(f"{base}/admin/")).text

        # One of an unknown provider is approved only once the admin confirms it.
        stranger_ugid = stranger_game.fields["UGID"]
        confirm = f"{base}/admin/servers/{stranger_ugid}/approve"
        asked = await client.post(confirm, data={"csrf": form_token(home.text)})
        asked_at = asked.headers["location"]
        assert asked_at == f"/admin/servers/{stranger_ugid}/approve?back=%2Fadmin%2F"
        question = await client.get(base + asked_at)
        assert "Approve this server?" in question.text
        assert "The provider id matches no provider on this instance." in question.text
        assert 'name="back" value="/admin/"' in question.text
        # The way back never leads to another site.
        elsewhere = await client.get(confirm, params={"back": "https://invented.example/admin/"})
        assert 'name="back" value="/admin/"' in elsewhere.text
        answer = {"csrf": form_token(question.text), "confirmed": "1", "back": "/admin/"}
        approved = await client.post(confirm, data=answer)
        home = await client.get(base + approved.headers["location"])
        assert "Approved Invented Stranger." in home.text
        assert "Nothing needs attention." in home.text

        # A revoked server that keeps trying to host shows as a warning.
        await cli(capsys, "servers", "revoke", stranger_ugid)
        secret = stranger_game.fields["SECRET"]
    async with game(server_url) as again:
        await connect_server(again)
        refused = await create(again, stranger_ugid, secret, "Invented Stranger")
        assert refused.error is not None
        async with httpx2.AsyncClient() as client:
            home = await sign_in(client, base)
        assert "Revoked · refused 1 time today" in home.text
        assert "The command line" in home.text
        assert "Nothing needs attention." not in home.text


async def test_the_home_needs_a_session_and_a_fresh_form(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        approve = f"{base}/admin/servers/00000000-0000-0000-0000-000000000000/approve"
        assert (await client.post(approve, data={})).status_code == 303
        home = await sign_in(client, base)
        assert (await client.post(approve, data={"csrf": "invented"})).status_code == 403
        missing = await client.post(approve, data={"csrf": form_token(home.text)})
        assert missing.status_code == 404
