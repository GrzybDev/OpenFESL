from datetime import UTC, datetime

import httpx2
import pytest
from accounts import PASSWORD, execute, post_registration, post_sign_in, sign_out
from identity import form_token
from support import FESL, Mailbox, cli, game, login

pytestmark = pytest.mark.anyio


async def test_registration_waits_for_its_link_and_answers_every_address_alike(
    mailbox: Mailbox, base: str, server_url: str, database_url: str
) -> None:
    async with httpx2.AsyncClient() as client:
        refused = await post_registration(
            client,
            base,
            email="cat@example",
            password="short",
            display_name="Invented Cat",
            birth_day="31",
            birth_month="2",
            terms="",
        )
        assert refused.status_code == 400
        assert "5 fields need a change." in refused.text
        for error in (
            "An e-mail address has the form name@example.net; this one does not.",
            "A game password has 8 to 31 characters; the one sent had 5.",
            "A display name uses only A-Z, a-z, 0-9, _ and -; this one has a space.",
            "31 February does not exist.",
            "Registration needs the terms accepted.",
        ):
            assert error in refused.text
        # The other fields come back; the game password never does.
        assert 'value="Invented Cat"' in refused.text
        assert 'value="short"' not in refused.text

        await execute(
            database_url,
            "UPDATE account SET display_name = 'Invented_Ann' WHERE nuid = 'ann@example.test'",
        )
        taken = await post_registration(client, base, display_name="invented_ann")
        assert "Another account uses invented_ann, ignoring case." in taken.text
        # Every game's limit counts, and nothing of the birth date is kept.
        young = await post_registration(
            client, base, country="DE", birth_year=str(datetime.now(UTC).year - 13)
        )
        assert young.status_code == 403
        assert "Accounts in Germany can be registered from the age of 14." in young.text

        # A new address and one that has an account get the same page; only the mails differ.
        for address, name in (("cat@example.test", "Invented_Cat"), ("ann@example.test", "Kit_2")):
            sent = await post_registration(client, base, email=address, display_name=name)
            assert sent.status_code == 200
            assert f"A mail went to {address}." in sent.text
        mail, link = await mailbox.receive("cat@example.test")
        assert "It can sign in and log in from games once" in mail.get_content()
        in_use, _ = await mailbox.receive("ann@example.test")
        assert "already has an OpenFESL account. Nothing was changed" in in_use.get_content()
        # The address in use made nothing, so the display name it was sent with is still free.
        free = await post_registration(client, base, email="dog@example.test", display_name="Kit_2")
        assert "A mail went to dog@example.test." in free.text
        await mailbox.receive("dog@example.test")
        paused = await post_registration(
            client, base, email="ann@example.test", display_name="Kit_3"
        )
        assert paused.status_code == 429
        assert "Registration with this address is paused for a few minutes." in paused.text

        # Until its link's page confirms the address, the account signs in neither here nor in
        # games. Opening the link only asks, as mail scanners open links too.
        assert (await post_sign_in(client, base, "cat@example.test", PASSWORD)).status_code == 403
        async with game(server_url) as player:
            await player.hello(FESL, "bfbc2-pc")
            for _ in range(2):
                page = await client.get(f"{base}{link}")
                assert "of the OpenFESL account Invented_Cat." in page.text
                assert page.headers["cache-control"] == "no-store"
            forged = await client.post(f"{base}{link}", data={"csrf": "forged"})
            assert forged.status_code == 400
            waiting = await player.request(FESL, "acct", login("cat@example.test", PASSWORD))
            assert waiting["errorCode"] == "105"
            form = {"csrf": form_token(page.text)}
            confirmed = await client.post(f"{base}{link}", data=form)
            assert "The account can now sign in here and log in from games." in confirmed.text
            assert (await client.post(f"{base}{link}", data=form)).status_code == 404
            # On to the game's key prompt, as for any new account.
            ready = await player.request(FESL, "acct", login("cat@example.test", PASSWORD))
            assert ready["errorCode"] == "120"
        assert (await post_sign_in(client, base, "cat@example.test", PASSWORD)).status_code == 303
    assert not mailbox.messages


async def test_without_mail_registration_is_offered_only_while_the_site_policy_is_off(
    base: str, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx2.AsyncClient() as client:
        unavailable = await client.get(f"{base}/register")
        assert "Registration needs the server&#39;s mail, which is not set up." in unavailable.text

        await cli(capsys, "settings", "set", "site.accounts.email_verification", "off")
        created = await post_registration(client, base)
        assert created.headers["location"] == "/account"
        shown = (await client.get(f"{base}/terms")).text
        assert "Account created." in shown and "Invented_Cat" in shown
        await sign_out(client, base)
        # Signed in at once, an address in use is named, as games name it.
        taken = await post_registration(
            client, base, email="ANN@example.test", display_name="Kit_2"
        )
        assert "Another account uses this e-mail address." in taken.text
        # No link could go out, so none is kept and the account never waits for one.
        await cli(capsys, "settings", "set", "site.accounts.email_verification", "on")
        assert (await post_sign_in(client, base, "cat@example.test", PASSWORD)).status_code == 303

    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        answer = await player.request(FESL, "acct", login("cat@example.test", PASSWORD))
        assert answer["errorCode"] == "120"
