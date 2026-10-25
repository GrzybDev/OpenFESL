from pathlib import Path

import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import cli, connect_server, create, game, masked, report

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-3", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def test_servers_move_between_tabs_through_the_admins_decisions(
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
        game(server_url) as hosted,
        game(server_url) as stranger,
    ):
        await connect_server(hosted)
        hosted_game = await create(hosted, name="Invented Night Rush")
        provider = ("B-U-Provider", masked(known_id, bytes(8)))
        await report(hosted, hosted_game, provider, ("B-U-level", "Levels/MP_006"))
        await connect_server(stranger)
        stranger_game = await create(stranger, name="Invented Stranger")
        hosted_ugid = hosted_game.fields["UGID"]
        stranger_ugid = stranger_game.fields["UGID"]

        page = (await sign_in(client, base, next="/admin/servers")).text
        assert "Invented Night Rush" in page and "Invented Stranger" in page
        assert (
            "The provider id matches Invented Hosting, which has no approved servers yet." in page
        )
        assert "The server has not reported a provider id yet." in page
        # The menu counts what waits for a decision.
        assert 'class="menu-count">2<' in page

        # A search looks through every tab, by part of a name or the start of an id.
        found = (await client.get(f"{base}/admin/servers", params={"q": "night"})).text
        assert "Invented Night Rush" in found and "Invented Stranger" not in found
        by_id = (await client.get(f"{base}/admin/servers", params={"q": stranger_ugid[:8]})).text
        assert "Invented Stranger" in by_id and "Invented Night Rush" not in by_id

        # A server's own address opens its row in its tab.
        opened = await client.get(f"{base}/admin/servers/{hosted_ugid}")
        assert opened.headers["location"].endswith(f"open={hosted_ugid}#row-{hosted_ugid}")
        assert "data-open" in (await client.get(base + opened.headers["location"])).text

        # Rejecting asks first, then moves the server to the revoked servers.
        reject = f"{base}/admin/servers/{stranger_ugid}/reject"
        asked = await client.post(reject, data={"csrf": form_token(page), "back": "/admin/servers"})
        question = await client.get(base + asked.headers["location"])
        assert "Reject this server?" in question.text
        answer = {"csrf": form_token(page), "confirmed": "1", "back": "/admin/servers"}
        await client.post(reject, data=answer)
        revoked = await client.get(f"{base}/admin/servers", params={"state": "revoked"})
        assert "Rejected Invented Stranger." in revoked.text
        assert "Invented Stranger" in revoked.text and "Invented Night Rush" not in revoked.text
        assert "Revoked · " in revoked.text and "by invented-keeper" in revoked.text

        # A known provider's server is approved at once, and its game lists right away.
        approve = f"{base}/admin/servers/{hosted_ugid}/approve"
        await client.post(approve, data={"csrf": form_token(page), "back": "/admin/servers"})
        listed = await client.get(f"{base}/bfbc2/servers")
        assert "Invented Night Rush" in listed.text

        # A decision about a server whose state changed meanwhile changes nothing.
        stale = await client.post(
            f"{base}/admin/servers/{hosted_ugid}/reject",
            data={"csrf": form_token(page), "confirmed": "1", "back": "/admin/servers"},
        )
        after = await client.get(base + stale.headers["location"])
        assert "Invented Night Rush was not changed" in after.text

        # Revoking asks first, and its game leaves the server list at once.
        revoke = f"{base}/admin/servers/{hosted_ugid}/revoke"
        question = await client.get(revoke)
        assert "Revoke this server?" in question.text
        assert "Approved · hosting now" in question.text
        await client.post(revoke, data={"csrf": form_token(page), "confirmed": "1"})
        assert "Invented Night Rush" not in (await client.get(f"{base}/bfbc2/servers")).text

        # A revoked server is approved again only once the admin confirms it.
        asked = await client.post(approve, data={"csrf": form_token(page)})
        assert asked.headers["location"].startswith(f"/admin/servers/{hosted_ugid}/approve?")
        again = await client.get(base + asked.headers["location"])
        assert "Approving it again lists its game" in again.text
        await client.post(approve, data={"csrf": form_token(page), "confirmed": "1"})
        approved = await client.get(f"{base}/admin/servers", params={"state": "approved"})
        assert "Invented Night Rush" in approved.text
        assert 'class="menu-count"' not in approved.text
