import httpx2
import pytest
from identity import IdentityProvider, sign_in
from support import FESL, THEATER, enter_theater, game, log_in_persona, log_in_player

pytestmark = pytest.mark.anyio
WARDEN = {"sub": "warden-5", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}


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
