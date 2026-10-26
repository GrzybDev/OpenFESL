import re
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import (
    FESL,
    THEATER,
    cli,
    enter_as_player,
    enter_theater,
    game,
    host_game,
    let_in,
    log_in_persona,
    log_in_player,
    login,
    registration,
)

pytestmark = pytest.mark.anyio
WARDEN = {"sub": "warden-5", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}
KEEPER = {"sub": "keeper-5", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def test_the_admin_finds_accounts_by_name_and_sees_what_each_one_holds(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    admin_provider.user = WARDEN
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with (
        httpx2.AsyncClient() as client,
        game(server_url) as ann,
        game(server_url) as bob,
    ):
        _persona, lkey = await log_in_persona(ann, FESL, "ann@example.test", "LanternAnn")
        await enter_theater(ann, THEATER, "bfbc2-pc", lkey)
        await log_in_player(bob, FESL, "lantern.bob@example.test")

        # Without a search the newest account comes first.
        page = (await sign_in(client, base, next="/admin/accounts")).text
        assert page.index("lantern.bob@example.test") < page.index("ann@example.test")
        assert "Accounts: 2" in page and "Newest first" in page
        oldest = (await client.get(f"{base}/admin/accounts", params={"order": "asc"})).text
        assert oldest.index("ann@example.test") < oldest.index("lantern.bob@example.test")

        # The start of a login name or an in-game name finds an account, in any case.
        found = (await client.get(f"{base}/admin/accounts", params={"q": "LANTERN"})).text
        assert "2 accounts match “LANTERN”." in found
        assert '<mark class="search-match">Lantern</mark>Ann' in found
        by_login = (await client.get(f"{base}/admin/accounts", params={"q": "ann@"})).text
        assert "1 account matches “ann@”." in by_login and "lantern.bob" not in by_login
        assert "In the menus" in by_login
        none = (await client.get(f"{base}/admin/accounts", params={"q": "ghost"})).text
        assert "No account matches “ghost”." in none

        account = next(
            line.split('href="')[1].split('"')[0]
            for line in by_login.splitlines()
            if 'class="account-link"' in line
        )
        shown = (await client.get(base + account)).text
        assert "ann@example.test" in shown and "LanternAnn" in shown
        # What ACTIVATE-GAME granted, the session with its client, and the remembered login.
        assert "ONLINE_ACCESS" in shown and "DR:156691300" in shown
        assert "Online · 1 session" in shown and "bfbc2-pc</code>" in shown
        assert "Remembered logins" in shown and "No login is remembered." not in shown
        assert "Current version accepted" in shown

        polish = (await client.get(f"{base}/pl{account}")).text
        assert "Zalogowane · 1 sesja" in polish and "W menu" in polish

        missing = await client.get(f"{base}/admin/accounts/9999")
        assert missing.status_code == 404
        assert "No account has the id 9999." in missing.text


async def test_moderators_decide_about_accounts_their_sessions_logins_and_names(
    admin_provider: IdentityProvider,
    open_hosting: None,
    server_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin_provider.user = WARDEN
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        async with (
            game(server_url) as server,
            game(server_url) as ann,
        ):
            created = await host_game(server)
            await enter_as_player(ann, "ann@example.test", "Ann_01")
            pid = await let_in(server, ann, created)
            page = (await sign_in(client, base, next="/admin/accounts?q=ann")).text
            account = re.search(r'href="(/admin/accounts/\d+)" class="account-link"', page)
            assert account
            address = base + account.group(1)
            shown = (await client.get(address)).text
            token = form_token(shown)
            assert "On Invented Server #1" in shown
            persona = re.search(r"/names/(\d+)/kick", shown)
            assert persona

            # A kick asks first, then takes the name out of its game at once.
            kick = f"{address}/names/{persona.group(1)}/kick"
            asked = await client.post(kick, data={"csrf": token})
            question = (await client.get(base + asked.headers["location"])).text
            assert "Kick this name from the game?" in question and "Invented Server #1" in question
            done = await client.post(kick, data={"csrf": token, "confirmed": "1"})
            kicked = await client.get(base + done.headers["location"])
            assert "Kicked Ann_01 from the game." in kicked.text
            assert (await server.theater_push(THEATER, "KICK")).fields["PID"] == pid

            # Ending the session lets the game connect again; only its own Goodbye says why.
            end = f"{address}/session/end"
            done = await client.post(end, data={"csrf": token, "confirmed": "1"})
            assert "Ended the session of ann@example.test." in (await client.get(address)).text
            assert (await ann.push("Goodbye"))["reason"] == "0"
            await ann.closed(FESL)

        again = login("ann@example.test", "secret-pass")
        async with game(server_url) as ann:
            await ann.hello(FESL, "bfbc2-pc")
            assert "errorCode" not in await ann.request(FESL, "acct", again)

        # A ban whose end has passed is refused; one with a later end refuses logins.
        async with game(server_url) as ann:
            await ann.hello(FESL, "bfbc2-pc")
            state = f"{address}/state"
            past = await client.post(
                state, data={"csrf": token, "state": "banned", "until": "2000-01-01T00:00"}
            )
            assert past.status_code == 400 and "The end must be a later date and time." in past.text
            assert "Change state" in (await client.get(state)).text
            until = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
            await client.post(state, data={"csrf": token, "state": "banned", "until": until})
            banned = (await client.get(address)).text
            assert "Banned ann@example.test." in banned and "Banned until" in banned
            assert (await ann.request(FESL, "acct", again))["errorCode"] == "103"
            await client.post(state, data={"csrf": token, "state": "active"})
            assert "errorCode" not in await ann.request(FESL, "acct", again)

            # A disabled name is refused with the game's text for it until it is enabled.
            persona_base = f"{address}/names/{persona.group(1)}"
            await client.post(f"{persona_base}/disable", data={"csrf": token, "confirmed": "1"})
            name = [("TXN", "NuLoginPersona"), ("name", "Ann_01")]
            assert (await ann.request(FESL, "acct", name))["errorCode"] == "164"
            await client.post(f"{persona_base}/enable", data={"csrf": token})
            assert "errorCode" not in await ann.request(FESL, "acct", name)

        # Remembered logins go one by one or all at once.
        remembered = (await client.get(address)).text
        first = re.search(r'name="login" value="([0-9a-f]+)"', remembered)
        assert first
        forget = f"{address}/remembered/remove"
        await client.post(forget, data={"csrf": token, "login": first.group(1)})
        assert (
            "Removed remembered logins of ann@example.test: 1." in (await client.get(address)).text
        )
        await client.post(forget, data={"csrf": token})
        assert "No login is remembered." in (await client.get(address)).text

        # An address waiting for its link is confirmed by hand.
        await cli(capsys, "settings", "set", "bfbc2.accounts.email_verification", "on")
        async with game(server_url) as bob:
            await bob.hello(FESL, "bfbc2-pc")
            terms = (await bob.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
            await bob.request(FESL, "acct", registration("bob@example.test", "secret-pass", terms))
            found = (await client.get(f"{base}/admin/accounts", params={"q": "bob@"})).text
            bob_account = re.search(r'href="(/admin/accounts/\d+)" class="account-link"', found)
            assert bob_account
            waiting = (await client.get(base + bob_account.group(1))).text
            assert "Waiting for confirmation" in waiting
            await client.post(f"{base}{bob_account.group(1)}/verify", data={"csrf": token})
            confirmed = (await client.get(base + bob_account.group(1))).text
            assert "Confirmed the address of bob@example.test." in confirmed
            assert "Waiting for confirmation" not in confirmed


async def test_admins_grant_and_revoke_entitlements_which_moderators_only_see(
    admin_provider: IdentityProvider, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with game(server_url) as ann:
        await log_in_player(ann, FESL, "ann@example.test")
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        page = (await sign_in(client, base, next="/admin/accounts?q=ann")).text
        account = re.search(r'href="(/admin/accounts/\d+)" class="account-link"', page)
        assert account
        address = base + account.group(1)
        token = form_token(page)

        # Revoking asks first; the game then refuses the login that needs the entitlement.
        revoke = f"{address}/entitlements/revoke"
        asked = await client.post(revoke, data={"csrf": token, "tag": "ONLINE_ACCESS"})
        assert (
            "Revoke this entitlement?" in (await client.get(base + asked.headers["location"])).text
        )
        await client.post(revoke, data={"csrf": token, "tag": "ONLINE_ACCESS", "confirmed": "1"})
        revoked = (await client.get(address)).text
        assert "Revoked ONLINE_ACCESS." in revoked and "· invented-keeper" in revoked
        again = login("ann@example.test", "secret-pass")
        async with game(server_url) as ann:
            await ann.hello(FESL, "bfbc2-pc")
            assert (await ann.request(FESL, "acct", again))["errorCode"] == "120"
            await client.post(
                f"{address}/entitlements/restore", data={"csrf": token, "tag": "ONLINE_ACCESS"}
            )
            assert "errorCode" not in await ann.request(FESL, "acct", again)

        # A key's grants come without using the key up; held tags are skipped.
        form = (await client.get(f"{address}/grant")).text
        assert "1 tag · already held" in form
        await client.post(f"{address}/grant", data={"csrf": token, "key": "ACTIVATE-VIETNAM"})
        granted = (await client.get(address)).text
        assert (
            "Granted BFBC2:PC:VIETNAM_ACCESS, BFBC2:PC:VIETNAM_PDLC to ann@example.test." in granted
        )
        assert "uses 0 of unlimited" in await cli(capsys, "keys", "limit", "ACTIVATE-VIETNAM")
        missing = await client.post(f"{address}/grant", data={"csrf": token, "key": "tag"})
        assert missing.status_code == 400 and "Tag is required." in missing.text
        one = {"csrf": token, "key": "tag", "tag": "INVENTED:TAG", "group": "Invented"}
        await client.post(f"{address}/grant", data=one)
        assert "INVENTED:TAG" in (await client.get(address)).text

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = (await sign_in(client, base, next=account.group(1))).text
        assert "Entitlements are changed by the admin role." in seen
        assert "Grant entitlements" not in seen and "BFBC2:PC:VIETNAM_PDLC" in seen
        refused = await client.post(
            f"{address}/grant", data={"csrf": form_token(seen), "key": "tag"}
        )
        assert refused.status_code == 403
        assert "Entitlements are granted and revoked by the admin role." in refused.text
