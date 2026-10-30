import re
from pathlib import Path
from xml.etree import ElementTree

import httpx2
import pytest
from accounts import execute, select_value
from heroes import MAGMA
from identity import IdentityProvider, sign_in
from store import ROYAL_SOLDIER, listed, shopper, stock, without_rotation
from support import fetch, visible
from test_admin_heroes import WARDEN

from openfesl.core.store import Bundle
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio


@pytest.mark.usefixtures("prompt_actions")
async def test_a_drop_that_falls_below_its_boxs_price_is_skipped_in_rolls_and_flagged(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    products: SqlProductStore,
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    # The pet that the invented catalogue holds of the class supply drops costs less than them.
    await execute(
        database_url, "UPDATE store_product SET price = 500 WHERE item_id = 521 AND type = 'unlm'"
    )
    box = await select_value(
        database_url, "SELECT id FROM store_box WHERE key = 'royal-soldier-supply-drop'"
    )
    assert isinstance(box, int)
    drop = Bundle("Royal Soldier Supply Drop", None, "_PF", 629, faction="royal", box=box)
    number = await products.add_bundle("bfheroes", drop)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)

    # Rolls skip the pet, so the hero gets the tonics that the box gives instead.
    path = f"/ofb/purchase/{hero}/OFB-DBFH:{number}"
    answer = ElementTree.fromstring((await fetch(server_url, path, MAGMA, cookie, b""))[2])
    assert [listed(given) for given in answer] == ["OFB-DBFH:12 2153-lmct x110 770_PF"]

    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        home = visible((await sign_in(client, base, next="/admin/")).content)
        assert "Store 3 needing attention" in home
        assert "Store 3 Boxes · entry below the price" in home
        assert "Invented Monkey, worth 500 of 629 · skipped in rolls" in home
        boxes = await client.get(f"{base}/admin/store/bfheroes/boxes")
        listed_boxes = visible(boxes.content)
        # Each section is named by its own heading, so no id is used twice.
        ids = re.findall(r'\sid="([^"]+)"', boxes.text)
        assert len(ids) == len(set(ids))
        assert "3 entries below their box's price" in listed_boxes
        assert "Invented Monkey, permanent, is worth 500 Funds; the box costs 629." in listed_boxes
        assert "Random boxes 8 boxes" in listed_boxes and "Treasure chest 1 box" in listed_boxes
        page = visible((await client.get(f"{base}/admin/store/bfheroes/boxes/{box}")).content)
        assert "Every entry is worth at least the price" in page
        assert "Invented Monkey item 521 Permanent 500 Funds 0.8" in page
        assert "the price Below the price · skipped" in page
        assert "Invented Tonic item 2153 110 uses" in page
        assert (await client.get(f"{base}/admin/store/bfheroes/boxes/999999")).status_code == 404
