import httpx2
import pytest
from accounts import PASSWORD, execute, post_registration, post_sign_in, returned
from identity import IdentityProvider, form_token
from support import FESL, Mailbox, cli, entitle_game, game, log_in_persona, login, markup

from openfesl.core.games import load_games
from openfesl.core.limits import FAILURES
from openfesl.core.stats import bucket_of, partition_of
from openfesl.protocol.rank import Period

pytestmark = pytest.mark.anyio


async def test_the_account_page_shows_the_profile_and_the_games_and_changes_the_profile(
    server_url: str, database_url: str, base: str
) -> None:
    async with game(server_url) as player:
        persona, _ = await log_in_persona(player, FESL, "kim@example.test", "Invented_Kim")
    bfbc2 = load_games().by_id("bfbc2")
    assert bfbc2 is not None
    await execute(
        database_url,
        "INSERT INTO stat (partition, owner_type, owner_id, period, period_start, key, value, text)"
        " VALUES (:partition, 1, :persona, 0, :start, 'sc_general', 2400, '')",
        partition=partition_of(bfbc2),
        persona=persona,
        start=bucket_of(Period.ALL_TIME, 0).start,
    )
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
        assert 'href="/bfbc2/players/Invented_Kim"' in shown
        assert "1 soldier" in shown and "No heroes" in shown
        assert "2,400 score" in shown

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


async def test_the_game_password_changes_with_the_current_one_and_goes_only_with_a_way_left(
    services: tuple[IdentityProvider, IdentityProvider],
    server_url: str,
    database_url: str,
    base: str,
) -> None:
    await execute(
        database_url,
        "UPDATE account SET display_name = 'Invented_Ann' WHERE nuid = 'ann@example.test'",
    )
    async with httpx2.AsyncClient() as client, httpx2.AsyncClient() as elsewhere:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        await post_sign_in(elsewhere, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}/account")
        assert "Set · used by" in markup(page.content)
        form = {
            "csrf": form_token(page.text),
            "current_password": "wrong-pass",
            "password": "short",
        }
        refused = await client.post(f"{base}/account/game-password", data=form)
        assert refused.headers["location"] == "/account#sign-in"
        page = await client.get(f"{base}/account")
        assert "2 fields need a change." in page.text
        assert "This is not the account&#39;s current game password." in page.text
        assert "A game password has 8 to 31 characters; the one sent had 5." in page.text

        # The other browser's session and the logins the game remembers end; this one stays.
        async with game(server_url) as player:
            await player.hello(FESL, "bfbc2-pc")
            await player.request(
                FESL, "acct", entitle_game("ann@example.test", PASSWORD, "ACTIVATE-GAME")
            )
            answer = await player.request(FESL, "acct", login("ann@example.test", PASSWORD))
            token = answer["encryptedLoginInfo"]
        changed = await client.post(
            f"{base}/account/game-password",
            data={**form, "current_password": PASSWORD, "password": "Kettle-Lamp-77"},
        )
        assert changed.headers["location"] == "/account#sign-in"
        assert "Game password changed." in (await client.get(f"{base}/account")).text
        assert (await elsewhere.get(f"{base}/account")).headers["location"].startswith("/sign-in")
        async with game(server_url) as player:
            await player.hello(FESL, "bfbc2-pc")
            remembered = [
                ("TXN", "NuLogin"),
                ("returnEncryptedInfo", "0"),
                ("encryptedInfo", token),
            ]
            assert (await player.request(FESL, "acct", remembered))["errorCode"] == "122"

        # The game password is the last way in until a configured service is linked; an
        # identity of a service the deployment no longer configures signs nothing in.
        await execute(
            database_url,
            "INSERT INTO sign_in_identity (service, subject, account_id, name)"
            " SELECT 'retired', 'r-ann', id, 'ann-retired' FROM account"
            " WHERE nuid = 'ann@example.test'",
        )
        removal = {"csrf": form["csrf"], "confirmed": "1"}
        kept = await client.post(f"{base}/account/game-password/remove", data=removal)
        assert kept.headers["location"] == "/account#sign-in"
        assert "The last way in cannot be removed." in (await client.get(f"{base}/account")).text
        await execute(
            database_url,
            "INSERT INTO sign_in_identity (service, subject, account_id, name)"
            " SELECT 'plain', '4711', id, 'ann-plain' FROM account WHERE nuid = 'ann@example.test'",
        )
        question = await client.get(f"{base}/account/game-password/remove")
        assert "Remove the game password?" in question.text
        removed = await client.post(f"{base}/account/game-password/remove", data=removal)
        assert removed.headers["location"] == "/account#sign-in"
        page = await client.get(f"{base}/account")
        assert "Game password removed; the account signs in through Invented Plain." in page.text
        assert "logins to Bad Company 2 and Heroes are refused until one is set" in page.text
        async with game(server_url) as player:
            await player.hello(FESL, "bfbc2-pc")
            answer = await player.request(FESL, "acct", login("ann@example.test", "Kettle-Lamp-77"))
            assert answer["errorCode"] == "122"

        # Setting it again needs no current one.
        set_again = await client.post(
            f"{base}/account/game-password",
            data={"csrf": form["csrf"], "password": "Lamp-Kettle-88"},
        )
        assert set_again.headers["location"] == "/account#sign-in"
        assert "Game password set." in (await client.get(f"{base}/account")).text


async def test_a_wrong_current_game_password_counts_toward_the_addresss_pause(
    database_url: str, base: str
) -> None:
    await execute(
        database_url,
        "UPDATE account SET display_name = 'Invented_Bob' WHERE nuid = 'bob@example.test'",
    )
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "bob@example.test", PASSWORD)
        page = await client.get(f"{base}/account")
        form = {
            "csrf": form_token(page.text),
            "current_password": "wrong-pass",
            "password": "Kettle-Lamp-77",
        }
        for _ in range(FAILURES):
            await client.post(f"{base}/account/game-password", data=form)
        await client.post(
            f"{base}/account/game-password", data={**form, "current_password": PASSWORD}
        )
        assert (
            "changing it is paused for a few minutes" in (await client.get(f"{base}/account")).text
        )
    async with httpx2.AsyncClient() as other:
        assert (await post_sign_in(other, base, "bob@example.test", PASSWORD)).status_code == 429


async def test_services_link_to_the_account_once_and_unlink_unless_they_are_the_last_way_in(
    services: tuple[IdentityProvider, IdentityProvider], database_url: str, base: str
) -> None:
    connect, plain = services
    await execute(
        database_url,
        "UPDATE account SET display_name = 'Invented_Ann' WHERE nuid = 'ann@example.test'",
    )
    await execute(
        database_url,
        "INSERT INTO sign_in_identity (service, subject, account_id, name)"
        " SELECT 'connect', 'c-bob', id, 'bob-connect' FROM account"
        " WHERE nuid = 'bob@example.test'",
    )
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}/account")
        csrf = form_token(page.text)
        assert page.text.count('action="/account/services/link"') == 1

        async def link(service: str) -> str:
            started = await client.post(
                f"{base}/account/services/link", data={"csrf": csrf, "service": service}
            )
            done = await returned(client, base, started)
            assert done.headers["location"] == "/account#sign-in"
            return (await client.get(f"{base}/account")).text

        plain.user = {"id": 4711, "login": "ann-plain"}
        assert "Invented Plain linked as ann-plain." in await link("plain")
        # An identity opens one account, which the refusal does not name.
        connect.user = {"sub": "c-bob"}
        refused = await link("connect")
        assert "This Invented Connect account already opens another OpenFESL account." in refused
        assert "bob" not in refused
        connect.cancel = True
        assert "Linking Invented Connect did not complete." in await link("connect")

        # With the game password gone, the service is the last way in and stays, as an identity
        # of a service the deployment no longer configures signs nothing in.
        await execute(
            database_url,
            "INSERT INTO sign_in_identity (service, subject, account_id, name)"
            " SELECT 'retired', 'r-ann', id, 'ann-retired' FROM account"
            " WHERE nuid = 'ann@example.test'",
        )
        removal = {"csrf": csrf, "confirmed": "1"}
        await client.post(f"{base}/account/game-password/remove", data=removal)
        kept = await client.post(f"{base}/account/services/plain/unlink", data={"csrf": csrf})
        assert kept.headers["location"] == "/account#sign-in"
        shown = (await client.get(f"{base}/account")).text
        assert "The last way in cannot be removed." in shown
        assert "Invented Plain is the last way in." in shown
        await client.post(f"{base}/account/services/retired/unlink", data={"csrf": csrf})
        assert "retired unlinked." in (await client.get(f"{base}/account")).text

        await client.post(
            f"{base}/account/game-password", data={"csrf": csrf, "password": "Lamp-Kettle-88"}
        )
        await client.post(f"{base}/account/services/plain/unlink", data={"csrf": csrf})
        shown = (await client.get(f"{base}/account")).text
        assert "Invented Plain unlinked. Link a service lists it again." in shown
        assert shown.count('name="service"') == 2
