from pathlib import Path

import httpx2
import pytest
from accounts import select_value
from identity import IdentityProvider, sign_in
from store import ROYAL_SOLDIER, buy, grant, shopper, stock
from support import visible
from test_admin_heroes import WARDEN, account_of

pytestmark = pytest.mark.anyio


@pytest.mark.usefixtures("prompt_actions")
async def test_the_panel_lists_wallets_and_their_ledgers_without_opening_any(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await grant(database_url, hero, "_PF", 5000)
    assert await buy(server_url, cookie, hero, 1) == ("success", None)
    ann = await account_of(database_url, "ann@example.test")
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        account = visible((await sign_in(client, base, next=f"/admin/accounts/{ann}")).content)
        assert (
            "Wallets Battlefield Heroes Funds 2,400 Valor Points 0" in account
            and "Wallets and ledgers" in account
        )
        shown = visible((await client.get(f"{base}/admin/accounts/{ann}/wallets")).content)
        # The Valor Points wallet was never used, and looking at it opens nothing.
        assert "Valor Points _DV · Account · Not opened yet" in shown
        assert "Hero Points _DH · Brassbuckle" in shown and "0 of 0" in shown
        wallets = await select_value(
            database_url, "SELECT count(*) FROM wallet WHERE currency = '_DV'"
        )
        assert wallets == 0

        ledger = f"{base}/admin/accounts/{ann}/ledger"
        funds = visible(
            (await client.get(ledger, params={"game": "bfheroes", "wallet": "_PF"})).content
        )
        assert "Funds · newest first 2 entries" in funds
        assert "Store purchase Invented Coat · purchase 1" in funds
        assert "Admin grant" in funds and "+5,000" in funds
        bought = visible(
            (await client.get(ledger, params={"wallet": "_PF", "purchases": "1"})).content
        )
        assert "1 entry" in bought and "+5,000" not in bought and "1 of 2" in bought
        points = await client.get(ledger, params={"wallet": f"_DH-{hero}"})
        assert "The wallet has no change yet." in visible(points.content)
        assert (await client.get(ledger, params={"wallet": "_XX"})).status_code == 404
