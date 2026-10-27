import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import cli, connect_server, create, fetch, game, png_image
from test_game_icons import BITMAP, ico
from test_settings import hello_error

from openfesl.core.game_icons import MAX_ICON_SIZE

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-7", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-7", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}
DEFAULTS = {
    "registration.age_check": "on",
    "registration.under_parental_age": "allow",
    "accounts.email_verification": "",
    "accounts.concurrent_login": "replace",
    "connect.minimum_client_version": "",
    "connect.minimum_hook_version": "",
}


async def test_admins_change_who_can_host_and_the_rules_that_moderators_only_see(
    admin_provider: IdentityProvider, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client, game(server_url) as waiting:
        await connect_server(waiting)
        await create(waiting, name="Invented Waiting")
        admin_provider.user = KEEPER
        page = (await sign_in(client, base, next="/admin/settings")).text
        token = form_token(page)
        assert "Servers waiting: 1" in page and "Invented Sign-in" in page
        assert "Invented Waiting" not in (await client.get(f"{base}/bfbc2/servers")).text

        # Opening hosting asks first, then lists the waiting server's game at once.
        hosting = f"{base}/admin/settings/hosting"
        asked = await client.post(hosting, data={"csrf": token, "mode": "open"})
        assert "Change who can host?" in (await client.get(base + asked.headers["location"])).text
        await client.post(hosting, data={"csrf": token, "mode": "open", "confirmed": "1"})
        assert "Any server can host now." in (await client.get(f"{base}/admin/settings")).text
        assert "Invented Waiting" in (await client.get(f"{base}/bfbc2/servers")).text
        assert "open" in await cli(capsys, "hosting")

        # Rules apply from their next use; a value equal to the default removes the override.
        rules = f"{base}/admin/settings/games/bfbc2"
        wrong = await client.post(
            rules, data={**DEFAULTS, "csrf": token, "connect.minimum_client_version": "newest"}
        )
        assert wrong.status_code == 400 and "A version is numbers with dots" in wrong.text
        on = {**DEFAULTS, "csrf": token, "connect.maintenance": "on"}
        await client.post(rules, data=on)
        changed = (await client.get(f"{base}/admin/settings")).text
        assert "Saved the rules of Battlefield: Bad Company 2." in changed
        assert "Maintenance on" in changed
        assert await hello_error(server_url, "bfbc2-pc") == "-253"
        await client.post(rules, data={**DEFAULTS, "csrf": token})
        assert await hello_error(server_url, "bfbc2-pc") == "0"
        assert "(default" not in await cli(capsys, "settings", "list")

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = (await sign_in(client, base, next="/admin/settings")).text
        assert "Settings are changed with the admin role." in seen
        refused = await client.post(
            f"{base}/admin/settings/hosting", data={"csrf": form_token(seen), "mode": "approved"}
        )
        assert refused.status_code == 403


async def test_admins_store_replace_and_remove_a_game_icon_that_moderators_only_see(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    icon = f"{base}/admin/settings/games/bfbc2/icon"
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        page = (await sign_in(client, base, next="/admin/settings")).text
        token = form_token(page)
        # Without PUBLIC_URL the server knows only the address it was reached at.
        assert f"address is {base}, so presence shows no images." in page

        # A refused file changes nothing, and the form names it with the reason.
        too_large = png_image(16, 16) + bytes(MAX_ICON_SIZE)
        for name, data, reason in [
            ("invented-old.ico", ico(BITMAP), "The ICO file holds bitmaps only."),
            ("invented-cover.gif", b"GIF89a" + bytes(32), "neither a PNG image nor an ICO file"),
            ("invented-large.png", too_large, "The image is larger than 1 MB"),
        ]:
            refused = await client.post(icon, data={"csrf": token}, files={"icon": (name, data)})
            assert refused.status_code == 400
            assert reason in refused.text and name in refused.text
        assert (await fetch(server_url, "/bfbc2/icon.png"))[0] == 404

        largest = png_image(64, 64)
        upload = ico(png_image(16, 16), largest)
        await client.post(icon, data={"csrf": token}, files={"icon": ("invented.ico", upload)})
        stored = (await client.get(f"{base}/admin/settings")).text
        assert "Icon set for Battlefield: Bad Company 2." in stored
        assert "PNG, 64 \N{MULTIPLICATION SIGN} 64, 1 KB" in stored and "invented-keeper" in stored
        assert f"{base}/bfbc2/icon.png" in stored
        assert (await fetch(server_url, "/bfbc2/icon.png"))[2] == largest

        replaced = png_image(128, 128)
        await client.post(icon, data={"csrf": token}, files={"icon": ("invented.png", replaced)})
        assert (await fetch(server_url, "/bfbc2/icon.png"))[2] == replaced

        await client.post(f"{icon}/remove", data={"csrf": token})
        removed = (await client.get(f"{base}/admin/settings")).text
        assert "Icon removed from Battlefield: Bad Company 2." in removed
        assert (await fetch(server_url, "/bfbc2/icon.png"))[0] == 404

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = (await sign_in(client, base, next="/admin/settings")).text
        assert "Changing the icon needs the admin role." in seen
        attempt = {"icon": ("invented.png", png_image(32, 32))}
        refused = await client.post(icon, data={"csrf": form_token(seen)}, files=attempt)
        assert refused.status_code == 403
        assert (await fetch(server_url, "/bfbc2/icon.png"))[0] == 404
