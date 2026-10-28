import httpx2
import pytest
from accounts import PASSWORD, execute, post_registration, post_sign_in
from identity import form_token
from support import FESL, Mailbox, cli, game, log_in_persona, login, markup

pytestmark = pytest.mark.anyio


async def test_the_account_page_shows_the_profile_and_changes_it(
    server_url: str, database_url: str, base: str
) -> None:
    async with game(server_url) as player:
        await log_in_persona(player, FESL, "kim@example.test", "Invented_Kim")
    await execute(
        database_url,
        "UPDATE account SET display_name = CASE nuid WHEN 'kim@example.test' THEN 'Kim_Online'"
        " ELSE 'Taken_Name' END WHERE nuid IN ('kim@example.test', 'ann@example.test')",
    )
    async with httpx2.AsyncClient() as client:
        asked = await client.get(f"{base}/account")
        assert asked.headers["location"] == "/sign-in?next=%2Faccount"
        assert (
            "The account page needs a signed-in account."
            in (await client.get(f"{base}/sign-in")).text
        )
        signed_in = await post_sign_in(client, base, "kim@example.test", PASSWORD)
        assert signed_in.headers["location"] == "/account"
        page = await client.get(f"{base}/account")
        shown = markup(page.content)
        assert "Kim_Online · one account for every game" in shown
        assert "kim@example.test" in shown

        # Names are compared ignoring case, and a refused name comes back in its open fold.
        form = {"csrf": form_token(page.text), "display_name": "taken_name"}
        taken = await client.post(f"{base}/account/display-name", data=form)
        assert taken.headers["location"] == "/account#profile"
        page = await client.get(f"{base}/account")
        assert "Another account uses taken_name, ignoring case." in page.text
        assert 'value="taken_name"' in page.text
        assert "Another account uses" not in (await client.get(f"{base}/account")).text

        renamed = await client.post(
            f"{base}/account/display-name", data={**form, "display_name": "Kim_Renamed"}
        )
        assert renamed.headers["location"] == "/account#profile"
        assert "Display name saved: Kim_Renamed." in (await client.get(f"{base}/account")).text
        moved = await client.post(
            f"{base}/pl/account/country", data={"csrf": form["csrf"], "country": "de"}
        )
        assert moved.headers["location"] == "/pl/account#profile"
        page = await client.get(f"{base}/pl/account")
        assert "Zapisano kraj: Niemcy." in page.text
        assert "Kim_Renamed · jedno konto do wszystkich gier" in markup(page.content)

    # Games receive the new country with the account's details.
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        await player.request(FESL, "acct", login("kim@example.test", PASSWORD))
        details = await player.request(FESL, "acct", [("TXN", "NuGetAccount")])
        assert details["country"] == "DE"


async def test_an_account_that_has_not_opened_its_link_is_offered_a_new_one_once_in_a_while(
    mailbox: Mailbox, base: str, capsys: pytest.CaptureFixture[str]
) -> None:
    await cli(capsys, "settings", "set", "site.accounts.email_verification", "off")
    async with httpx2.AsyncClient() as client:
        created = await post_registration(client, base)
        assert created.headers["location"] == "/account"
        await mailbox.receive("cat@example.test")
        page = await client.get(f"{base}/account")
        assert "The e-mail address of this account is not confirmed yet." in page.text
        assert "Accounts made on this site do not wait for it" in page.text
        # The registration's mail went out a moment ago, so the next one waits.
        form = {"csrf": form_token(page.text)}
        waits = await client.post(f"{base}/account/confirmation", data=form)
        assert waits.headers["location"] == "/account#profile"
        assert "Another one can be sent from" in (await client.get(f"{base}/account")).text
        assert not mailbox.messages
