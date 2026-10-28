from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from playwright.async_api import Browser
from store import ROYAL_SOLDIER, stock, without_rotation
from support import visible

from openfesl.core.store import Bundle
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio

DEALS = "/bfheroes/store/hot-deals"
# The invented catalogue's products: the Royal coat for good, the National tunic, the Royal pet
# and 25 bandages.
COAT, TUNIC, PET, BANDAGES = 1, 3, 5, 6


@pytest.mark.usefixtures("prompt_actions")
async def test_the_hot_deals_page_lists_each_shelf_and_expires_a_deal_whose_time_runs_out(
    base: str,
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    async with httpx2.AsyncClient() as client:
        empty = visible((await client.get(f"{base}{DEALS}")).content)
    now = datetime.now(UTC)
    box = await select_value(
        database_url, "SELECT id FROM store_box WHERE key = 'royal-soldier-supply-drop'"
    )
    assert isinstance(box, int)
    for bundle in (
        Bundle("Invented Plain Offer", None, "_PF", 300, members=(TUNIC,)),
        Bundle(
            "WEST_Custom_Bundle_Invented",
            "WEST_Custom_Bundle_Invented_HotDealsDesc",
            "_PF",
            2000,
            original_price=3675,
            faction="royal",
            ends=now + timedelta(days=1, hours=2),
            members=(COAT, PET, BANDAGES),
            section="sale",
        ),
    ):
        await products.add_bundle("bfheroes", bundle)
    box_deal = await products.add_bundle(
        "bfheroes",
        Bundle(
            "Royal Soldier Supply Drop", None, "_PF", 629, faction="royal", box=box, section="boxes"
        ),
    )
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Mudlark")

    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{DEALS}")).content)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        signed_in = visible((await client.get(f"{base}{DEALS}")).content)
        polish = visible((await client.get(f"{base}/pl{DEALS}")).content)
        item = await client.get(f"{base}/bfheroes/store/buy/{box_deal}")
        context = await edge.new_context(locale="en-GB")
        for name, value in client.cookies.items():
            await context.add_cookies([{"name": name, "value": value, "url": base}])
        await execute(database_url, "DELETE FROM setting WHERE key = 'bfheroes.deals.enabled'")
        rotating = visible((await client.get(f"{base}{DEALS}")).content)

    # Without the rotation the admin's offers are listed alone, in the shelves they name and the
    # others after them.
    assert "No hot deals right now Every item stays in the store at its usual price." in empty
    assert "Offers set up by the administrators of this service. Every deal is bought" in signed_out
    assert "Deal of the Day changes" not in signed_out
    assert signed_out.index("On sale") < signed_out.index("Random boxes")
    assert signed_out.index("Random boxes") < signed_out.index("More offers")
    assert (
        "Invented Set -45% 45% off was 3,675 2,000 Funds Invented Coat · permanent "
        "Invented Monkey · permanent Invented Bandage · 25 uses Requires Royal Army heroes "
        "Ends in 1 d"
    ) in signed_out
    assert "Royal Soldier Supply Drop 629 Funds 1 item, worth at least 629 Funds" in signed_out
    assert "Heroes For" not in signed_out
    # Signed in, each card names the account's heroes who may have it.
    assert "Requires Royal Army heroes Heroes For Brassbuckle Ends in" in signed_in
    assert (
        "Invented Plain Offer 300 Funds Invented Tunic · permanent Requires Any hero "
        "Heroes For Brassbuckle and Mudlark No end date"
    ) in signed_in
    assert "Wyprzedaż" in polish and "Dla: Brassbuckle" in polish
    # A box's page leads back to the hot deals.
    assert 'href="/bfheroes/store/hot-deals" class="store-back"' in item.text
    assert "Deal of the Day changes at 00:00 UTC, new sets come every Monday" in rotating

    # With scripts the time left counts down, red once under a day, and a deal whose time runs
    # out says so.
    for name, left in (("Invented Short Offer", 4), ("Invented Day Offer", 86_403)):
        ends = datetime.now(UTC) + timedelta(seconds=left)
        offer = Bundle(name, None, "_PF", 100, ends=ends, members=(PET,), section="sale")
        await products.add_bundle("bfheroes", offer)
    page = await context.new_page()
    # The browser's clock is an hour ahead of the server's, which the countdowns keep to.
    await page.clock.set_system_time(datetime.now(UTC) + timedelta(hours=1))
    await page.goto(base + DEALS)
    card = page.locator("[data-deal]", has_text="Invented Short Offer")
    await card.get_by_text("It leaves the list at the next page load.").wait_for(timeout=8000)
    said = await card.inner_text()
    assert "expired" in said and "Expired at" in said and "Choose a hero" not in said
    sale = page.locator("[data-deal]", has_text="Invented Set")
    assert "Ends in" in await sale.inner_text()
    assert not await sale.locator(".deal-timer-soon").count()
    day = page.locator("[data-deal]", has_text="Invented Day Offer")
    assert await day.locator(".deal-timer-soon").count() == 1
