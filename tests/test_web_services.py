import json
from collections.abc import AsyncIterator

import httpx2
import pytest
from accounts import PASSWORD, execute, sign_in_through, sign_out
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


async def follow(
    client: httpx2.AsyncClient, base: str, started: httpx2.Response
) -> httpx2.Response:
    """Follow a sign-in from the service's redirect to the page the site then opens."""
    assert started.status_code == 302, started.text
    back = await client.get(started.headers["location"])
    done = await client.get(back.headers["location"])
    assert done.status_code == 303, done.text
    return await client.get(f"{base}{done.headers['location']}")


async def test_a_verified_address_of_an_account_links_its_service_only_after_a_proof(
    services: tuple[IdentityProvider, IdentityProvider],
    base: str,
    database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connect, plain = services
    async with httpx2.AsyncClient() as client:
        # A service's address finds an account only when the service marks it verified.
        connect.user = {"sub": "c-bob", "email": "bob@example.test", "email_verified": False}
        unverified = await sign_in_through(client, base, "connect")
        assert unverified.headers["location"] == "/register/service"

        connect.user = {
            "sub": "c-ann",
            "preferred_username": "ann-connect",
            "email": "ann@example.test",
            "email_verified": True,
        }
        asked = await sign_in_through(client, base, "connect", next="/terms")
        assert asked.headers["location"] == "/sign-in/proof"
        page = await client.get(f"{base}/sign-in/proof")
        assert "ann@example.test belongs to the OpenFESL account ann@example.test." in page.text
        wrong = await client.post(
            f"{base}/sign-in/proof", data={"csrf": form_token(page.text), "password": "x"}
        )
        assert wrong.status_code == 400
        assert "This is not the game password of ann@example.test." in wrong.text
        linked = await client.post(
            f"{base}/sign-in/proof", data={"csrf": form_token(page.text), "password": PASSWORD}
        )
        assert linked.headers["location"] == "/terms"
        shown = (await client.get(f"{base}/terms")).text
        assert "Invented Connect linked as ann-connect. Signed in as ann@example.test." in shown
        await sign_out(client, base)
        assert (await sign_in_through(client, base, "connect")).headers["location"] == "/account"
        await sign_out(client, base)

        # A linked service proves the account too, but only with the identity linked to it; the
        # address comes from the service's list, which marks only one of them verified.
        plain.user = {"id": 77, "login": "ann-plain"}
        plain.emails = [
            {"email": "elsewhere@example.test", "primary": True, "verified": False},
            {"email": "ann@example.test", "primary": False, "verified": True},
        ]
        assert (await sign_in_through(client, base, "plain")).headers["location"] == (
            "/sign-in/proof"
        )
        page = await client.get(f"{base}/sign-in/proof")
        assert "Sign in with Invented Connect to link Invented Plain" in page.text
        prove = {"csrf": form_token(page.text), "service": "connect"}
        # An identity of a service that is no longer configured proves nothing.
        await execute(
            database_url,
            "INSERT INTO sign_in_identity (service, subject, account_id, name)"
            " SELECT 'retired', 'r-ann', id, 'ann-retired' FROM account"
            " WHERE nuid = 'ann@example.test'",
        )
        retired = {**prove, "service": "retired"}
        assert (await client.post(f"{base}/sign-in/proof/service", data=retired)).headers[
            "location"
        ] == "/sign-in"
        connect.user = {"sub": "c-someone-else"}
        other = await follow(
            client, base, await client.post(f"{base}/sign-in/proof/service", data=prove)
        )
        assert "Invented Connect answered with an account that is not linked to" in other.text
        connect.user = {"sub": "c-ann"}
        done = await follow(
            client, base, await client.post(f"{base}/sign-in/proof/service", data=prove)
        )
        assert "Invented Plain linked as ann-plain." in done.text
        await sign_out(client, base)

        # A deactivated account says so only after a right proof, and nothing is linked.
        await cli(capsys, "accounts", "state", "bob@example.test", "deactivated")
        connect.user = {"sub": "c-bob", "email": "bob@example.test", "email_verified": True}
        await sign_in_through(client, base, "connect")
        page = await client.get(f"{base}/sign-in/proof")
        refused = await client.post(
            f"{base}/sign-in/proof", data={"csrf": form_token(page.text), "password": PASSWORD}
        )
        assert refused.status_code == 403
        assert "Invented Connect was not linked" in refused.text
        cancelled = await client.post(
            f"{base}/sign-in/proof/cancel", data={"csrf": form_token(page.text)}
        )
        assert cancelled.headers["location"] == "/sign-in"
        assert (await client.get(f"{base}/sign-in/proof")).headers["location"] == "/sign-in"
