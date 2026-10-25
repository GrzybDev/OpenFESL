import re
from pathlib import Path

import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import cli, connect_server, create, game, masked, report

from openfesl.core.servers import provider_file

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-4", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-4", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}


async def test_an_admin_adds_a_provider_hands_out_its_file_and_revokes_it(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        page = (await sign_in(client, base, next="/admin/providers")).text
        assert "No provider is added yet." in page

        added = await client.post(
            f"{base}/admin/providers", data={"csrf": form_token(page), "name": "Invented Hosting"}
        )
        created = await client.get(base + added.headers["location"])
        assert "Provider added" in created.text and "Invented Hosting" in created.text
        found = re.search(r"/admin/providers/([0-9a-f]{32})/provider-id\.dat", created.text)
        assert found
        provider_id = bytes.fromhex(found.group(1))
        download = await client.get(f"{base}{found.group(0)}")
        assert download.content == provider_file(provider_id)
        assert 'filename="ProviderId.dat"' in download.headers["content-disposition"]

        # Names are unique without regard to case.
        again = await client.post(
            f"{base}/admin/providers", data={"csrf": form_token(page), "name": "invented HOSTING"}
        )
        assert again.status_code == 400
        assert "A provider named Invented Hosting already exists" in again.text

        # A server started with the file lists under the provider.
        async with game(server_url) as server:
            await connect_server(server)
            hosted = await create(server, name="Invented Night Rush")
            await report(server, hosted, ("B-U-Provider", masked(provider_id, bytes(8))))
            await approve_in_panel(base, client, hosted.fields["UGID"], page)
            listed = (await client.get(f"{base}/admin/providers")).text
            assert (
                "Invented Night Rush" in listed and "1 approved · 0 waiting · 0 revoked" in listed
            )
            assert "Invented Night Rush" in (await client.get(f"{base}/bfbc2/servers")).text

            # Revoking asks first, then takes its servers' games off the list at once.
            revoke = f"{base}/admin/providers/{provider_id.hex()}/revoke"
            question = await client.get(revoke)
            assert "Revoke this provider?" in question.text
            assert "Its only server is revoked" in question.text
            done = await client.post(revoke, data={"csrf": form_token(page), "confirmed": "1"})
            after = await client.get(base + done.headers["location"])
            assert "Revoked Invented Hosting and its servers." in after.text
            assert "Invented Night Rush" not in (await client.get(f"{base}/bfbc2/servers")).text

        revoked = await client.get(f"{base}/admin/providers", params={"state": "revoked"})
        assert "Invented Hosting" in revoked.text and "by invented-keeper" in revoked.text
        assert (await client.get(f"{base}{found.group(0)}")).status_code == 404


async def approve_in_panel(base: str, client: httpx2.AsyncClient, ugid: str, page: str) -> None:
    """Approve a server from the servers page, as the admin does."""
    approve = f"{base}/admin/servers/{ugid}/approve"
    await client.post(approve, data={"csrf": form_token(page), "back": "/admin/servers"})


async def test_moderators_see_providers_but_do_not_manage_them(
    admin_provider: IdentityProvider,
    server_url: str,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    out = tmp_path / "ProviderId.dat"
    await cli(capsys, "providers", "add", "Invented Hosting", "--out", str(out))
    provider_id = out.read_bytes()[:16].hex()
    # The command line keeps names unique too, and leaves no file behind.
    taken = tmp_path / "Taken.dat"
    with pytest.raises(SystemExit, match="A provider named invented hosting exists already"):
        await cli(capsys, "providers", "add", "invented hosting", "--out", str(taken))
    assert not taken.exists()

    admin_provider.user = WARDEN
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        page = (await sign_in(client, base, next="/admin/providers")).text
        assert "Invented Hosting" in page
        assert "Providers are added and revoked with the admin role." in page
        assert "Add a provider" not in page
        refused = await client.post(
            f"{base}/admin/providers", data={"csrf": form_token(page), "name": "Invented Other"}
        )
        assert refused.status_code == 403
        assert "Providers and their files are managed by the admin role." in refused.text
        download = await client.get(f"{base}/admin/providers/{provider_id}/provider-id.dat")
        assert download.status_code == 403
