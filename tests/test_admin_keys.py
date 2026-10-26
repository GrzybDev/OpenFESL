import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import FESL, game, log_in_player

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-6", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-6", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}


async def test_admins_add_disable_and_enable_product_keys_that_moderators_only_see(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        page = (await sign_in(client, base, next="/admin/keys")).text
        token = form_token(page)
        assert "ACTIVATE-VETERAN" in page and "5 of 5 here" in page and "Keys: 5" in page

        # New codes are unique in any case and keep to the characters the game's prompt takes.
        added = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "LAN-2026"})
        listed = (await client.get(base + added.headers["location"])).text
        assert "Added LAN-2026." in listed and "Added here" in listed
        taken = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "lan-2026"})
        assert taken.status_code == 400 and "A key with this code exists already" in taken.text
        wrong = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "a b"})
        assert wrong.status_code == 400

        # Disabling asks first; the game then refuses the key and the guide leaves it out.
        disable = f"{base}/admin/keys/ACTIVATE-VETERAN/disable"
        asked = await client.post(disable, data={"csrf": token})
        question = (await client.get(base + asked.headers["location"])).text
        assert "Disable this key?" in question and "BF3:PC:ADDSVETRANK" in question
        await client.post(disable, data={"csrf": token, "confirmed": "1"})
        disabled = (await client.get(f"{base}/admin/keys", params={"state": "disabled"})).text
        assert "Disabled ACTIVATE-VETERAN." in disabled and "· invented-keeper" in disabled
        assert "ACTIVATE-VETERAN" not in (await client.get(f"{base}/bfbc2/play")).text
        redeem = [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-VETERAN")]
        async with game(server_url) as ann:
            await log_in_player(ann, FESL, "ann@example.test")
            assert (await ann.request(FESL, "acct", redeem))["errorCode"] == "181"
            await client.post(f"{base}/admin/keys/ACTIVATE-VETERAN/enable", data={"csrf": token})
            assert "errorCode" not in await ann.request(FESL, "acct", redeem)

        again = await client.post(f"{base}/admin/keys/starting-keys", data={"csrf": token})
        assert (
            "Every starting key is here already."
            in (await client.get(base + again.headers["location"])).text
        )

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = (await sign_in(client, base, next="/admin/keys")).text
        assert "LAN-2026" in seen and "Add a key" not in seen
        assert "Product keys are added, edited, enabled and disabled with the admin role." in seen
        refused = await client.post(
            f"{base}/admin/keys/LAN-2026/disable", data={"csrf": form_token(seen)}
        )
        assert refused.status_code == 403
