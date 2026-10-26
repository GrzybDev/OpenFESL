import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import cli, connect_server, create, game
from test_settings import hello_error

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
