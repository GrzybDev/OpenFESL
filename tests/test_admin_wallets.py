from pathlib import Path

import httpx2
import pytest
from accounts import select_value
from identity import IdentityProvider, form_token, sign_in
from store import ROYAL_SOLDIER, buy, grant, shopper, stock
from support import visible
from test_admin_heroes import KEEPER, WARDEN, account_of, changed

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


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_grant_and_take_within_the_balance_a_game_reads(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    _, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    ann = await account_of(database_url, "ann@example.test")
    funds = f"{base}/admin/accounts/{ann}/wallets/bfheroes/_PF"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"/admin/accounts/{ann}")
        assert (await client.get(funds)).status_code == 403
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=f"/admin/accounts/{ann}")
        # A grant opens the wallet, so the allowance comes first.
        granted = await changed(client, funds, direction="grant", amount="1000", note="Ticket 61")
        assert "Granted 1,000 Funds to ann@example.test: 2,500 → 3,500." in granted
        asked = await client.get(funds)
        token = form_token(asked.text)
        for amount, direction, refusal in (
            ("9000", "take", "Not possible: Funds holds 3,500, so at most 3,500 can be taken."),
            ("2147483000", "grant", "and the game reads balances only up to 2,147,483,647."),
        ):
            sent = {"csrf": token, "direction": direction, "amount": amount, "note": "Twice"}
            refused = await client.post(funds, data=sent)
            assert refused.status_code == 400 and refusal in visible(refused.content)
        points = f"{base}/admin/accounts/{ann}/wallets/bfheroes/_DH-{hero}"
        await changed(client, points, direction="grant", amount="2", note="Level 16 brought none")
        taken = await changed(client, points, direction="take", amount="1", note="Counted twice")
        assert "Took 1 Hero Points from Brassbuckle: 2 → 1." in taken

        ledger = f"{base}/admin/accounts/{ann}/ledger"
        shown = visible((await client.get(ledger, params={"wallet": "_PF"})).content)
        assert "+1,000 Balance after 3,500 Admin grant invented-keeper Ticket 61" in shown
        assert "+2,500 Balance after 2,500 Starting allowance" in shown
        log = visible((await client.get(f"{base}/admin/audit", params={"account": ann})).content)
        assert "Wallet take Brassbuckle" in log
        assert "1 Hero Points, 2 → 1 · ledger entry" in log and "Counted twice" in log
        assert "Wallet grant" in log and "1,000 Funds, 2,500 → 3,500 · ledger entry" in log
