import re

import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import FESL, cli, game, log_in_persona

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-8", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}


async def test_the_audit_log_lists_every_change_by_admin_and_by_account(
    admin_provider: IdentityProvider, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with game(server_url) as ann:
        persona, _key = await log_in_persona(ann, FESL, "ann@example.test", "Ann_01")
    await cli(capsys, "hosting", "open")
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        page = (await sign_in(client, base, next="/admin/accounts?q=ann")).text
        token = form_token(page)
        account = re.search(r'href="/admin/accounts/(\d+)" class="account-link"', page)
        assert account
        number = account.group(1)
        await client.post(
            f"{base}/admin/accounts/{number}/state", data={"csrf": token, "state": "banned"}
        )
        names = f"{base}/admin/accounts/{number}/names/{persona}"
        await client.post(f"{names}/disable", data={"csrf": token, "confirmed": "1"})
        await client.post(
            f"{base}/admin/keys/ACTIVATE-VETERAN/disable", data={"csrf": token, "confirmed": "1"}
        )

        log = (await client.get(f"{base}/admin/audit")).text
        assert log.index("Disabled key") < log.index("Banned account")
        assert "Changed the hosting mode" in log and "The command line" in log
        assert "From Approved servers only to Any server" in log
        assert f"Account {number} · ann@example.test" in log and "invented-keeper" in log

        # One admin's changes, and one account's with what was done to its names.
        mine = (await client.get(f"{base}/admin/audit", params={"admin": "keeper-8"})).text
        assert "Disabled key" in mine and "Changed the hosting mode" not in mine
        theirs = (await client.get(f"{base}/admin/audit", params={"account": number})).text
        assert "Banned account" in theirs and "Disabled in-game name" in theirs
        assert "Disabled key" not in theirs and "Entries: 2" in theirs
        polish = (await client.get(f"{base}/pl/admin/audit")).text
        assert "Zablokowano konto" in polish and "Wiersz poleceń" in polish
