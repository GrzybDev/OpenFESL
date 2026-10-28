import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from heroes import MAGMA
from playwright.async_api import Browser, BrowserContext, Error, async_playwright
from store import ROYAL_SOLDIER, grant, shopper, stock

pytestmark = pytest.mark.anyio

# The script bridges that the game binds as window.game, each call recorded: 1.89's, and 1.46's,
# which lacks equip, keepOpen and showAds and takes a purchase's tag alone.
BRIDGE = """
window.game = {
  notifyPurchase: function () { record(["notifyPurchase"].concat([].slice.call(arguments))); },
  equip: function () { record(["equip"].concat([].slice.call(arguments))); },
  keepOpen: function () { record(["keepOpen"]); },
  showAds: function (on) { record(["showAds", on]); },
};
"""
OLD_BRIDGE = """
window.game = {
  notifyPurchase: function () { record(["notifyPurchase"].concat([].slice.call(arguments))); },
};
"""


@pytest.fixture
async def edge() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(channel="msedge")
        except Error:
            pytest.skip("Microsoft Edge, which runs the pages' scripts, is not installed")
        yield browser
        await browser.close()


async def game_window(
    browser: Browser, base: str, cookie: dict[str, str], bridge: str
) -> tuple[BrowserContext, list[list[Any]]]:
    """Open a browser as the game's window: through the hook, with the session and a bridge."""
    context = await browser.new_context(extra_http_headers={"X-OpenFESL-Host": MAGMA})
    session = cookie["Cookie"].removeprefix("magma=")
    await context.add_cookies([{"name": "magma", "value": session, "url": base}])
    calls: list[list[Any]] = []
    await context.expose_binding("record", lambda _, call: calls.append(call))
    await context.add_init_script(bridge)
    return context, calls


async def called(calls: list[list[Any]], count: int) -> list[list[Any]]:
    """Return the calls to the bridge once it has had as many, as they arrive on their own."""
    with anyio.fail_after(5):
        while len(calls) < count:
            await anyio.sleep(0.05)
    return calls


@pytest.mark.usefixtures("prompt_actions")
async def test_the_store_page_tells_the_game_of_what_was_bought(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    await stock(server_url, tmp_path, capsys)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await grant(database_url, hero, "_DV", 1000)

    context, calls = await game_window(edge, base, cookie, BRIDGE)
    page = await context.new_page()
    await page.goto(f"{base}/en/game/store?personaId={hero}&eqp=2005_3")
    await page.get_by_role("link", name=re.compile("Weapons")).click()
    await page.get_by_role("link", name=re.compile("Invented Rifle")).click()
    await page.get_by_role("button", name=re.compile("3 days")).click()
    # Picking a price point says what it leaves, without a new page.
    assert await page.get_by_text("the account keeps 500 Valor Points").is_visible()
    await page.get_by_role("button", name="Buy and equip").click()
    await page.wait_for_url(re.compile("bought="))
    await page.get_by_text("Bought and equipped").wait_for()
    # The game keeps the window open while buying, then loads and equips the item.
    assert await called(calls, 2) == [["keepOpen"], ["notifyPurchase", "3015-tlmd", True]]
    await context.close()

    context, calls = await game_window(edge, base, cookie, OLD_BRIDGE)
    page = await context.new_page()
    await page.goto(f"{base}/en/game/store/weapons/3015?personaId={hero}")
    # 1.46 cannot equip from the page, so the page offers buying alone.
    assert await page.get_by_role("button", name="Buy and equip").count() == 0
    await page.get_by_role("button", name=re.compile("1 day")).click()
    await page.get_by_role("button", name="Buy", exact=True).click()
    await page.get_by_text("Bought", exact=True).wait_for()
    assert await called(calls, 1) == [["notifyPurchase", "3015-tlmd"]]
    await context.close()
