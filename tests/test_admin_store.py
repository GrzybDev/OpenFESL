from pathlib import Path

import httpx2
import pytest
from accounts import execute, select_value
from identity import IdentityProvider, form_token, sign_in
from playwright.async_api import Browser
from store import listed, offers, stock
from support import visible
from test_admin_heroes import KEEPER, WARDEN

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


@pytest.mark.usefixtures("prompt_actions")
async def test_enter_in_the_add_panel_adds_the_price_point_without_scripts(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    await stock(server_url, tmp_path, capsys)
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next="/admin/store/bfheroes/items/3")
        context = await edge.new_context(locale="en-GB", java_script_enabled=False)
        for name, value in client.cookies.items():
            await context.add_cookies([{"name": name, "value": value, "url": base}])
    page = await context.new_page()
    await page.goto(f"{base}/admin/store/bfheroes/items/3")
    points = page.locator("[name^=price-]")
    assert await points.count() == 2

    await page.locator("details.prices-add > summary").click()
    await page.locator("#add-price").fill("lots")
    await page.locator("#add-price").press("Enter")
    await page.get_by_text("A price is from 1 to 2,147,483,647; lots is not.").wait_for()
    # The refused point leaves the saved points as they are.
    assert await page.locator("[name=sold-1]").is_checked()
    await page.locator("#add-price").fill("777")
    await page.locator("#add-price").press("Enter")
    await page.get_by_text("Added a price point to Invented Coat").wait_for()
    assert await points.count() == 3
    await context.close()


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_change_add_and_reset_an_items_price_points_that_the_game_then_sells(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    coat = f"{base}/admin/store/bfheroes/items/3"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next="/admin/store/bfheroes/items/3")
        token = form_token((await client.get(coat)).text)
        assert (await client.post(coat, data={"csrf": token})).status_code == 403
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        page = await sign_in(client, base, next="/admin/store/bfheroes/items/3")
        token = form_token(page.text)
        sent = {"csrf": token, "price-1": "2600", "sold-1": "1", "price-2": "0", "sold-2": "1"}
        refused = await client.post(coat, data=sent)
        assert refused.status_code == 400
        assert "A price is from 1 to 2,147,483,647; 0 is not." in visible(refused.content)
        changed = {"csrf": token, "price-1": "2,400", "sold-1": "1", "price-2": "1300"}
        saved = await client.post(coat, data=changed, follow_redirects=True)
        assert "Saved the prices of Invented Coat." in visible(saved.content)
        assert "OFB-DBFH:1 Currency Funds Ownership Permanent" in visible(saved.content)
        sold = [listed(product) for product in await offers(server_url)]
        assert "OFB-DBFH:1 3-unlm 2400_PF" in sold and "OFB-DBFH:2 3-tlmd 30d 1300_PF" not in sold

        add = {"csrf": token, "add-currency": "_DV", "add-ownership": "7d", "add-price": ""}
        unpriced = await client.post(f"{coat}/add", data=add)
        assert "A price is from 1 to 2,147,483,647; 0 is not." in visible(unpriced.content)
        added = await client.post(f"{coat}/add", data=add | {"add-price": "1000"})
        assert "Added a price point to Invented Coat: Valor Points, 7 days." in visible(
            (await client.get(base + added.headers["location"])).content
        )
        twice = visible((await client.post(f"{coat}/add", data=add | {"add-price": "9"})).content)
        assert "Valor Points, 7 days is already a price point of Invented Coat." in twice
        longer = await client.post(f"{coat}/add", data=add | {"add-ownership": "14d"})
        assert (
            "Not saved: 14 days is not a duration of the rules; they know 1, 3, 7 and 30 days"
            in visible(longer.content)
        )

        reset = await client.post(f"{coat}/reset", data={"csrf": token}, follow_redirects=True)
        assert "Reset the prices of Invented Coat to its kind's rule." in visible(reset.content)
        points = await select_value(
            database_url,
            "SELECT string_agg(id || ':' || price || ':' || published, ' ' ORDER BY id)"
            " FROM store_product WHERE item_id = 3",
        )
        assert isinstance(points, str)
        permanent, month, rental = (point.split(":") for point in points.split())
        assert permanent[1:] == ["2600", "true"] and month[1:] == ["1300", "true"]
        assert rental[1:] == ["1000", "false"]
        fields = {f"price-{point[0]}": point[1] for point in (permanent, month, rental)}
        off = await client.post(coat, data={"csrf": token, "unsold": "1", **fields})
        assert off.status_code == 303
        listed_off = visible((await client.get(f"{base}/admin/store/bfheroes?off=1")).content)
        assert "Invented Coat" in listed_off
        assert all(" 3-" not in listed(product) for product in await offers(server_url))
        withheld = f"{base}/admin/store/bfheroes/items/257"
        assert (await client.post(withheld, data={"csrf": token})).status_code == 400

        log = visible((await client.get(f"{base}/admin/audit")).content)
        assert "Prices reset to the rule Invented Coat · OFB-DBFH:1" in log
        assert "Funds, permanent: 2,400 → 2,600; Funds, 30 days: sold again" in log
        assert "Valor Points, 7 days: added at 1,000" in log
        assert "Funds, permanent: 2,600 → 2,400; Funds, 30 days: switched off" in log
