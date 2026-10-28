from datetime import timedelta

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, sign_in_through
from identity import IdentityProvider, form_token
from support import Mailbox

from openfesl.core import password_resets
from openfesl.core.limits import FAILURES

pytestmark = pytest.mark.anyio

NEW_PASSWORD = "renewed-pass"


async def request_reset(client: httpx2.AsyncClient, base: str, address: str) -> httpx2.Response:
    page = await client.get(f"{base}/password-reset")
    form = {"csrf": form_token(page.text), "email": address}
    return await client.post(f"{base}/password-reset", data=form)


async def test_a_reset_link_sets_a_new_game_password_once_and_ends_the_pause(
    mailbox: Mailbox, base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with httpx2.AsyncClient() as client:
        for _ in range(FAILURES):
            await post_sign_in(client, base, "ann@example.test", "wrong-pass")
        assert (await post_sign_in(client, base, "ann@example.test", PASSWORD)).status_code == 429

        # Every address gets the same page; only an account's address gets mail, once a while.
        for address in ("ann@example.test", "nobody@example.test", "ann@example.test"):
            asked = await request_reset(client, base, address)
            assert f"If an account uses {address}, a mail with a link" in asked.text
        mail, link = await mailbox.receive("ann@example.test")
        assert "This link sets it" in mail.get_content()

        page = await client.get(f"{base}{link}")
        assert "ann@example.test" in page.text
        assert page.headers["cache-control"] == "no-store"
        form = {"csrf": form_token(page.text), "password": "short"}
        short = await client.post(f"{base}{link}", data=form)
        assert short.status_code == 400
        assert "A game password has 8 to 31 characters; the one sent had 5." in short.text
        done = await client.post(f"{base}{link}", data={**form, "password": NEW_PASSWORD})
        shown = await client.get(f"{base}{done.headers['location']}")
        assert "Sign-in here and game logins now take the new game password." in shown.text
        assert (await client.get(f"{base}{link}")).status_code == 404

        # The new game password ends the pause; the old one no longer signs in.
        assert (await post_sign_in(client, base, "ann@example.test", PASSWORD)).status_code == 400
        signed_in = await post_sign_in(client, base, "ann@example.test", NEW_PASSWORD)
        assert signed_in.status_code == 303

    monkeypatch.setattr(password_resets, "VALID_FOR", timedelta(0))
    async with httpx2.AsyncClient() as client:
        await request_reset(client, base, "bob@example.test")
        _, expired = await mailbox.receive("bob@example.test")
        gone = await client.get(f"{base}{expired}")
        assert gone.status_code == 410
        assert "A reset link is valid for one hour." in gone.text
    assert not mailbox.messages


async def test_a_reset_unlinks_the_services_linked_while_the_address_waited_for_its_link(
    mailbox: Mailbox,
    services: tuple[IdentityProvider, IdentityProvider],
    base: str,
    database_url: str,
) -> None:
    # As after a registration with someone else's address and one's own service: the address
    # waits for its link, and the service is already linked.
    await execute(
        database_url,
        "UPDATE account SET verification_hash = 'x', verification_expires_at = now() + interval"
        " '1 day', display_name = 'Invented_Ann' WHERE nuid = 'ann@example.test'",
    )
    await execute(
        database_url,
        "INSERT INTO sign_in_identity (service, subject, account_id, name)"
        " SELECT 'connect', 'c-eve', id, 'Invented_Eve' FROM account"
        " WHERE nuid = 'ann@example.test'",
    )
    connect, _ = services
    connect.user = {"sub": "c-eve", "preferred_username": "Invented_Eve"}
    async with httpx2.AsyncClient() as client:
        await request_reset(client, base, "ann@example.test")
        _, link = await mailbox.receive("ann@example.test")
        page = await client.get(f"{base}{link}")
        form = {"csrf": form_token(page.text), "password": NEW_PASSWORD}
        done = await client.post(f"{base}{link}", data=form)
        shown = await client.get(f"{base}{done.headers['location']}")
        assert "the sign-in services linked meanwhile were unlinked" in shown.text
        signed_in = await sign_in_through(client, base, "connect")
        assert signed_in.headers["location"] == "/register/service"
