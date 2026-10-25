import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in

pytestmark = pytest.mark.anyio


async def test_admins_sign_in_through_their_groups_and_sign_out(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    provider = admin_provider
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        opened = await client.get(f"{base}/admin/")
        assert opened.status_code == 303
        assert opened.headers["location"] == "/admin/sign-in?next=%2Fadmin%2F"
        page = await client.get(f"{base}/admin/sign-in?next=/admin/")
        assert "INVENTED SIGN-IN" in page.text.upper()
        # The form leads to the provider, so the page's policy lets it.
        assert f"form-action 'self' {provider.url}" in page.headers["content-security-policy"]
        cookie = page.headers["set-cookie"]
        assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()

        # An account in no group of the panel gets no session.
        provider.user = {"sub": "visitor-1", "preferred_username": "invented-visitor", "groups": []}
        refused = await sign_in(client, base)
        assert (
            "The Invented Sign-in account invented-visitor has no access to this panel."
            in refused.text
        )
        assert (await client.get(f"{base}/admin/")).status_code == 303

        # A cancelled sign-in says so.
        provider.cancel = True
        page = await client.get(f"{base}/admin/sign-in")
        started = await client.post(f"{base}/admin/sign-in", data={"csrf": form_token(page.text)})
        back = await client.get(started.headers["location"])
        failed = await client.get(back.headers["location"])
        assert failed.status_code == 303
        page = await client.get(f"{base}{failed.headers['location']}")
        assert "Sign-in did not complete. Try again." in page.text
        provider.cancel = False

        # A token that the provider's keys did not sign gets no session.
        provider.forge = True
        provider.user = {"sub": "keeper-1", "preferred_username": "x", "groups": ["fesl-admins"]}
        forged = await sign_in(client, base)
        assert "Invented Sign-in returned without a signed-in account." in forged.text
        assert (await client.get(f"{base}/admin/")).status_code == 303
        provider.forge = False

        provider.user = {
            "sub": "warden-7",
            "preferred_username": "invented-warden",
            "groups": ["fesl-moderators"],
        }
        home = await sign_in(client, base)
        assert "Moderator · signed in through Invented Sign-in" in home.text
        await client.post(f"{base}/admin/sign-out", data={"csrf": form_token(home.text)})

        provider.user = {
            "sub": "keeper-1",
            "preferred_username": "invented-keeper",
            "groups": ["fesl-moderators", "fesl-admins"],
        }
        home = await sign_in(client, base, next="/pl/admin/")
        assert home.url.path == "/pl/admin/"
        assert "invented-keeper" in home.text
        assert "Rola administratora · zalogowano przez Invented Sign-in" in home.text

        # A form from another session changes nothing.
        expired = await client.post(f"{base}/admin/sign-out", data={"csrf": "invented"})
        assert expired.status_code == 403
        assert "The form expired. Reload the page and try again." in expired.text
        assert (await client.get(f"{base}/admin/")).status_code == 200

        signed_out = await client.post(
            f"{base}/admin/sign-out", data={"csrf": form_token(home.text)}
        )
        assert signed_out.status_code == 303
        page = await client.get(f"{base}{signed_out.headers['location']}")
        assert (
            "The panel&#39;s session has ended. Invented Sign-in keeps its own session" in page.text
        )
        assert (await client.get(f"{base}/admin/")).status_code == 303


@pytest.fixture
def unreachable_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before the server starts; nothing listens on the discard port.
    for key, value in {
        "ADMIN_OIDC_NAME": "Invented Sign-in",
        "ADMIN_OIDC_ISSUER": "http://127.0.0.1:9",
        "ADMIN_OIDC_CLIENT_ID": "panel-client",
        "ADMIN_OIDC_CLIENT_SECRET": "invented-secret",
        "ADMIN_GROUP": "fesl-admins",
    }.items():
        monkeypatch.setenv(key, value)


async def test_an_unreachable_provider_fails_the_sign_in_with_a_notice(
    unreachable_provider: None, server_url: str
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}/admin/sign-in")
        started = await client.post(f"{base}/admin/sign-in", data={"csrf": form_token(page.text)})
        assert started.status_code == 303
        page = await client.get(f"{base}{started.headers['location']}")
        assert "Invented Sign-in could not be reached." in page.text


async def test_an_instance_without_sign_in_says_so(server_url: str) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}/admin/sign-in")
        assert "Sign-in to the panel is not set up on this instance." in page.text
        assert (await client.get(f"{base}/admin/")).status_code == 303
