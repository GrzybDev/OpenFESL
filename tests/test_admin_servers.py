from pathlib import Path

import httpx2
import pytest
from heroes import create_hero, start
from identity import IdentityProvider, form_token, sign_in
from support import THEATER, cli, connect_server, create, game, masked, report
from test_player_servers import SERVER_GAME, register

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-3", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-3", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}


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


async def test_a_server_of_a_game_without_providers_is_approved_in_one_step(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    await register(server_url, "host@example.test")
    await create_hero(base, "host@example.test", "host")
    async with httpx2.AsyncClient() as client, game(server_url) as server:
        await start(server_url, server, "host@example.test", role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        ugid = created.fields["UGID"]

        # Battlefield Heroes servers report no provider, so none is unknown or missing.
        home = (await sign_in(client, base, next="/admin/")).text
        page = (await client.get(f"{base}/admin/servers", params={"open": ugid})).text
        reject = (await client.get(f"{base}/admin/servers/{ugid}/reject")).text
        for shown in (home, page, reject):
            assert "Invented Heroes Server" in shown
            assert "Unknown provider" not in shown and "unknown provider" not in shown
        assert "The server has not reported a provider id yet." not in page
        assert "Approve lists its game in the server list." in page

        approved = await client.post(
            f"{base}/admin/servers/{ugid}/approve",
            data={"csrf": form_token(page), "back": "/admin/servers"},
        )
        assert approved.headers["location"] == "/admin/servers"
        listed = await client.get(
            f"{base}/admin/servers", params={"state": "approved", "open": ugid}
        )
        assert "Approved Invented Heroes Server." in listed.text
        # The game shows the official mark in the menu only, which is all its drawer promises.
        assert "Shows the mark beside its name in the menu at once" in listed.text
        assert "on the server list" not in listed.text
        log = (await client.get(f"{base}/admin/audit")).text
        assert "Approved server" in log and "nknown provider" not in log


async def test_admins_mark_approved_servers_as_official_and_moderators_see_the_mark(
    admin_provider: IdentityProvider, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with (
        httpx2.AsyncClient() as client,
        httpx2.AsyncClient() as warden,
        game(server_url) as hosted,
        game(server_url) as stranger,
    ):
        await connect_server(hosted)
        ugid = (await create(hosted, name="Invented Night Rush")).fields["UGID"]
        await connect_server(stranger)
        waiting = (await create(stranger, name="Invented Stranger")).fields["UGID"]
        await cli(capsys, "servers", "approve", ugid)
        row = f"/admin/servers?state=approved&open={ugid}"

        page = (await sign_in(client, base, next=row)).text
        assert "Mark as official" in page
        assert (
            "Marking it also makes Bad Company 2 show its own server mark and filter for it."
            in page
        )
        others = (await client.get(f"{base}/admin/servers", params={"open": waiting})).text
        assert "Only approved servers can be marked." in others
        assert "Mark as official" not in others
        assert (
            await client.post(
                f"{base}/admin/servers/{waiting}/official",
                data={"csrf": form_token(page), "back": "/admin/servers"},
            )
        ).headers["location"] == "/admin/servers"
        assert (
            "Invented Stranger was not changed" in (await client.get(f"{base}/admin/servers")).text
        )

        # Marking needs no confirmation, and the row keeps its details open.
        marked = await client.post(
            f"{base}/admin/servers/{ugid}/official", data={"csrf": form_token(page), "back": row}
        )
        assert marked.headers["location"] == row
        page = (await client.get(base + row)).text
        assert "Marked Invented Night Rush as official." in page
        assert "Remove the official mark" in page and "Its official mark goes with it." in page
        assert "Bad Company 2 also shows its own server mark and filter for this server." in page
        question = (await client.get(f"{base}/admin/servers/{ugid}/revoke")).text
        assert "does not return when the server is approved again" in question

        # Moderators see the mark, and only the admin role changes it.
        admin_provider.user = WARDEN
        seen = (await sign_in(warden, base, next=row)).text
        assert "The official mark is changed by the admin role." in seen
        assert "Remove the official mark" not in seen
        refused = await warden.post(
            f"{base}/admin/servers/{ugid}/unofficial", data={"csrf": form_token(seen)}
        )
        assert refused.status_code == 403
        assert "The official mark is changed by the admin role." in refused.text

        removed = await client.post(
            f"{base}/admin/servers/{ugid}/unofficial", data={"csrf": form_token(page), "back": row}
        )
        page = (await client.get(base + removed.headers["location"])).text
        assert "Invented Night Rush is no longer marked as official." in page
        assert "Mark as official" in page
        log = (await client.get(f"{base}/admin/audit")).text
        assert log.index("Removed the official mark") < log.index("Marked server as official")
        assert "invented-keeper" in log
        polish = (await client.get(f"{base}/pl/admin/audit")).text
        assert "Usunięto oznaczenie oficjalnego serwera" in polish
