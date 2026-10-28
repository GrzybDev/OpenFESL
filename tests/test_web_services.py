import json
from collections.abc import AsyncIterator

import httpx2
import pytest
from accounts import execute, sign_in_through, sign_out
from identity import IdentityProvider, form_token, identity_provider
from support import cli, markup

pytestmark = pytest.mark.anyio


@pytest.fixture
async def services(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[IdentityProvider, IdentityProvider]]:
    # Before the server starts, which reads its sign-in services once: an OpenID Connect service
    # and a plain OAuth2 one that lists the account's addresses, as GitHub does.
    async with identity_provider() as connect, identity_provider() as plain:
        configured = [
            {
                "id": "connect",
                "name": "Invented Connect",
                "glyph": "globe",
                "issuer": connect.url,
                "client_id": "site-client",
                "client_secret": "invented-secret",
                "scope": "openid email profile",
            },
            {
                "id": "plain",
                "name": "Invented Plain",
                "kind": "oauth2",
                "glyph": "code",
                "authorize_url": f"{plain.url}/oauth/authorize",
                "token_url": f"{plain.url}/oauth/token",
                "userinfo_url": f"{plain.url}/api/user",
                "emails_url": f"{plain.url}/api/emails",
                "client_id": "site-client",
                "client_secret": "invented-secret",
                "scope": "read:user user:email",
                "subject_claim": "id",
                "name_claim": "login",
            },
        ]
        monkeypatch.setenv("SIGN_IN_SERVICES", json.dumps(configured))
        yield connect, plain


async def test_a_linked_identity_signs_in_at_once_and_a_new_one_goes_to_the_account_step(
    services: tuple[IdentityProvider, IdentityProvider],
    base: str,
    database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connect, plain = services
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}/sign-in")
        shown = markup(page.content)
        assert '<section aria-labelledby="sign-in-through"' in shown
        assert "Continues on 127.0.0.1" in shown
        assert "Invented Plain: profile and e-mail addresses." in shown
        policy = page.headers["content-security-policy"]
        assert f"form-action 'self' {connect.url} {plain.url}" in policy

        await execute(
            database_url,
            "INSERT INTO sign_in_identity (service, subject, account_id, name)"
            " SELECT 'plain', '4711', id, 'ann-plain' FROM account WHERE nuid = 'ann@example.test'",
        )
        plain.user = {"id": 4711, "login": "ann-plain"}
        signed_in = await sign_in_through(client, base, "plain", next="/pl/terms")
        assert signed_in.headers["location"] == "/pl/terms"
        assert "przez Invented Plain" in (await client.get(f"{base}/pl/terms")).text
        await sign_out(client, base)

        # An identity that no account uses goes to the step that creates one, verified or not.
        connect.user = {"sub": "new-1", "name": "Invented Newcomer", "email": "new@example.test"}
        created = await sign_in_through(client, base, "connect")
        assert created.headers["location"] == "/register/service"
        assert "Invented Newcomer" not in (await client.get(f"{base}/terms")).text

        connect.cancel = True
        cancelled = await sign_in_through(client, base, "connect")
        page = await client.get(f"{base}{cancelled.headers['location']}")
        assert "Sign-in through Invented Connect did not complete." in page.text
        connect.cancel = False

        await cli(capsys, "accounts", "state", "ann@example.test", "deactivated")
        refused = await sign_in_through(client, base, "plain")
        page = await client.get(f"{base}{refused.headers['location']}")
        assert "A deletion request deactivated it on " in page.text
        assert "przez Invented Plain" not in (await client.get(f"{base}/pl/terms")).text


@pytest.fixture
def unreachable_service(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before the server starts; nothing listens on the discard port.
    configured = {
        "id": "gone",
        "name": "Invented Gone",
        "issuer": "http://127.0.0.1:9",
        "client_id": "site-client",
        "client_secret": "invented-secret",
        "scope": "openid email",
    }
    monkeypatch.setenv("SIGN_IN_SERVICES", json.dumps([configured]))


async def test_an_unreachable_service_leaves_the_other_ways_in(
    unreachable_service: None, base: str
) -> None:
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}/sign-in")
        form = {"csrf": form_token(page.text), "service": "gone"}
        started = await client.post(f"{base}/sign-in/service", data=form)
        assert started.status_code == 303
        page = await client.get(f"{base}{started.headers['location']}")
        assert "Invented Gone could not be reached." in page.text
