import re
from pathlib import Path
from xml.etree import ElementTree

import httpx2
import pytest
from accounts import execute, select_value
from heroes import MAGMA
from identity import IdentityProvider, form_token, sign_in
from store import ROYAL_SOLDIER, listed, shopper, stock, without_rotation
from support import fetch, visible
from test_admin_heroes import KEEPER, WARDEN

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


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_change_a_drop_table_within_the_rules_every_table_keeps(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    # The chest of the game data, of which the invented catalogue holds the rifle, the tonics,
    # the bandages and the pet.
    chest = await select_value(database_url, "SELECT id FROM store_box WHERE kind = 'chest'")
    page = f"{base}/admin/store/bfheroes/boxes/{chest}"
    tiers = await select_value(
        database_url,
        "SELECT string_agg(DISTINCT tier || ':' || weight, ' ') FROM store_drop"
        " WHERE box_id = :box",
        box=chest,
    )
    assert tiers == "0:60 1:28 2:10 3:2"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible(
            (await sign_in(client, base, next=f"/admin/store/bfheroes/boxes/{chest}")).content
        )
        assert "Drop tables are changed with the admin role." in shown
        token = form_token((await client.get(page)).text)
        assert (await client.post(page, data={"csrf": token})).status_code == 403
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token(
            (await sign_in(client, base, next=f"/admin/store/bfheroes/boxes/{chest}")).text
        )
        weights = {"weight-0": "60", "weight-1": "28", "weight-2": "10", "weight-3": "2"}
        sent = {"csrf": token, "worth": "1", **weights, "weight-3": "1"}
        refused = await client.post(page, data=sent)
        assert refused.status_code == 400
        assert "Weights add up to 99%; they must add up to 100%" in visible(refused.content)

        found = visible((await client.get(page, params={"q": "invented c"})).content)
        assert "Invented Coat item 3" in found and "Permanent · 2,600" in found
        assert "Invented Logo Shirt" not in found
        cheap = {"csrf": token, "item": "2153", "ownership": "x25", "tier": "0"}
        below = await client.post(f"{page}/add", data=cheap)
        assert below.status_code == 400
        assert (
            "Invented Tonic, 25 uses, is worth 175 Funds, below the box's price of 499"
            in visible(below.content)
        )
        coat = {"csrf": token, "item": "3", "ownership": "perm", "tier": "2"}
        added = await client.post(f"{page}/add", data=coat, follow_redirects=True)
        assert "Saved the drop table of Treasure chest." in visible(added.content)
        assert "Invented Coat item 3 Permanent 2,600 Funds" in visible(added.content)

        rifle = await select_value(
            database_url,
            "SELECT id FROM store_product WHERE item_id = 3015 AND type = 'tlmd' AND days = 7"
            " AND currency = '_PF'",
        )
        removed = {"csrf": token, "worth": "1", **weights, f"remove-{rifle}": "1"}
        saved = await client.post(page, data=removed, follow_redirects=True)
        assert "Saved the drop table of Treasure chest." in visible(saved.content)
        log = visible((await client.get(f"{base}/admin/audit")).content)
        assert "Drop table changed treasure-chest Details removed Invented Rifle (7 days)" in log
        assert "added Invented Coat (permanent)" in log

        # A box whose own text promises its worth keeps the promise.
        mechanical = await select_value(
            database_url, "SELECT id FROM store_box WHERE key = 'royal-mechanical'"
        )
        off = await client.post(
            f"{base}/admin/store/bfheroes/boxes/{mechanical}", data={"csrf": token}
        )
        assert off.status_code == 400
        assert "so the promise stays on." in visible(off.content)
