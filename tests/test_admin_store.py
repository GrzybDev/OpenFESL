from pathlib import Path

import httpx2
import pytest
from accounts import execute
from identity import IdentityProvider, sign_in
from store import stock
from support import visible
from test_admin_heroes import WARDEN

pytestmark = pytest.mark.anyio


@pytest.mark.usefixtures("prompt_actions")
async def test_the_panel_lists_the_store_prices_with_their_rules_and_why_items_are_not_sold(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        empty = visible((await sign_in(client, base, next="/admin/store/bfheroes")).content)
        assert "Prices need the catalogue of Battlefield Heroes." in empty
        first = await client.get(f"{base}/admin/store")
        assert first.headers["location"] == "/admin/store/bfheroes"
    await stock(server_url, tmp_path, capsys)
    # The coat's 30 days at a price of its own; its rule makes it half of 2,600.
    await execute(database_url, "UPDATE store_product SET price = 999 WHERE id = 2")
    async with httpx2.AsyncClient() as client:
        listed = visible((await sign_in(client, base, next="/admin/store/bfheroes")).content)
        assert (
            "Invented Coat item 3 Kind Appearance Requires Royal Army, any class, any level Price"
            " points 2 price points Funds 30 days, permanent Own prices" in listed
        )
        assert "Valor Points 1, 3, 7, 30 days · Funds permanent On sale" in listed
        assert "Invented Dash item 2096" in listed and "3 steps Hero Points: 1 per step" in listed
        assert "Invented Knife item 3001" in listed and "None Starting kit Not sold" in listed
        assert "Invented Logo Shirt item 257" in listed
        assert "None Withheld · granted by hand only Not sold" in listed
        assert "Invented Mission" not in listed
        assert "Own prices 1 Fixed 1" in listed

        weapons = visible((await client.get(f"{base}/admin/store/bfheroes?kind=weapon")).content)
        assert "Invented Rifle" in weapons and "Invented Coat" not in weapons
        found = visible((await client.get(f"{base}/admin/store/bfheroes?q=invented+lo")).content)
        assert "Items 2" in found and "Invented Longrifle" in found
        assert "Invented Rifle" not in found
        own = visible((await client.get(f"{base}/admin/store/bfheroes?own=1")).content)
        assert "Items 1" in own and "Invented Coat" in own

        coat = visible((await client.get(f"{base}/admin/store/bfheroes/items/3")).content)
        assert "Rule 2,600 Funds" in coat and "Price points 2 · 1 own" in coat
        assert "OFB-DBFH:2 Currency Funds Ownership 30 days 999 From Own price rule: 1,300" in coat
        assert "OFB-DBFH:1 Currency Funds Ownership Permanent 2,600 From Rule Sold" in coat
        assert "Prices are changed with the admin role." in coat
        dash = visible((await client.get(f"{base}/admin/store/bfheroes/items/2096")).content)
        assert "Step 3 of 3 1 From Fixed Sold" in dash
        shirt = visible((await client.get(f"{base}/admin/store/bfheroes/items/257")).content)
        assert "never sold, rolled or rewarded" in shirt
        polish = visible((await client.get(f"{base}/pl/admin/store/bfheroes")).content)
        assert "Funds: 30 dni, na stałe Własne ceny" in polish
        assert (await client.get(f"{base}/admin/store/bfheroes/items/6000")).status_code == 404
        assert (await client.get(f"{base}/admin/store/bfbc2")).status_code == 404
