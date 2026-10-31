from pathlib import Path

import httpx2
import pytest
from accounts import select_value
from identity import IdentityProvider, form_token, sign_in
from living import import_catalogue
from support import visible
from test_admin_heroes import KEEPER, WARDEN
from test_claw import ITEMS

pytestmark = pytest.mark.anyio
BOXES = "/admin/store/bfheroes/boxes"
SETTINGS = {
    "runs": "1",
    "free": "1",
    "paid": "1",
    "currency": "_PF",
    "price": "599",
    "feed": "1",
    "length": "20",
}


async def test_admins_change_the_claw_and_its_prizes_with_a_note_and_moderators_read_them(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, {**ITEMS, 257: ITEMS[521]})
    pool = await select_value(database_url, "SELECT id FROM store_box WHERE kind = 'claw'")
    page = f"{BOXES}/{pool}"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        read = visible((await sign_in(client, base, next=BOXES)).content)
        read_pool = await client.get(f"{base}{page}")
        token = form_token(read_pool.text)
        forbidden = await client.post(f"{base}{BOXES[: -len('/boxes')]}/claw", data={"csrf": token})
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next=BOXES)).content)
        token = form_token((await client.get(f"{base}{BOXES}")).text)
        claw = f"{base}/admin/store/bfheroes/claw"
        refused = await client.post(claw, data={"csrf": token, **SETTINGS, "price": "0"})
        await client.post(
            claw,
            data={"csrf": token, **SETTINGS, "runs": "", "price": "499", "note": "Site load"},
        )
        after = visible((await client.get(f"{base}{BOXES}")).content)
        editor = visible((await client.get(f"{base}{page}?faction=royal&class=soldier")).content)
        withheld = visible((await client.get(f"{base}{page}?q=257")).content)
        weights = {"weight-0": "70", "weight-1": "25", "weight-2": "5", "rare-2": "1"}
        uneven = await client.post(
            f"{base}{page}", data={"csrf": token, **weights, "weight-0": "60", "note": "x"}
        )
        no_rare = await client.post(
            f"{base}{page}", data={"csrf": token, **weights, "rare-2": "", "note": "x"}
        )
        await client.post(
            f"{base}{page}",
            data={"csrf": token, **weights, "weight-0": "65", "weight-2": "10", "note": "Rarer"},
        )
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert "Daily claw machine WEST_Custom_DailySpinner" in read
    assert "The Claw runs Yes" in read and "Paid grabs Yes · 599 Funds" in read
    assert "The Claw is changed with the admin role." in read
    assert "Prize pool · 3 tiers, 3 entries" in read
    assert "Rare mark" not in visible(read_pool.content) and forbidden.status_code == 403
    assert "Free grabs a day" in shown and "the rewards' day" in shown
    assert "Free grabs today 0 Paid grabs today 0 Funds spent today 0" in shown
    assert refused.status_code == 400
    assert "A price is at least 1." in refused.text and "A note is required" in refused.text
    assert "Off: the Claw does not open" in after and "The Daily Claw is not running" in after
    assert 'value="499"' in (await _page(admin_provider, base, BOXES))
    assert "Common · 1 entry" in editor and "Rare · 1 entry · Rare" in editor
    assert "Invented Monkey item 521 Permanent Royal Army, any class, any level" in editor
    assert "Common Invented Tonic · 50 uses 70%" in editor
    assert "Uncommon Invented Rifle · 7 days 25%" in editor
    assert "Rare Invented Monkey · Permanent 5%" in editor
    assert "Item 257 is withheld on this service: codes, the Claw and rewards" in withheld
    assert uneven.status_code == 400 and "Weights add up to 90" in uneven.text
    assert no_rare.status_code == 400 and "At least one tier carries the rare mark" in no_rare.text
    tiers = "SELECT string_agg(tier || ':' || weight || ':' || rare, ' ' ORDER BY tier)"
    stored = await select_value(database_url, f"{tiers} FROM store_drop WHERE box_id = :b", b=pool)
    assert stored == "0:65:false 1:25:false 2:10:true"
    assert "Claw switched off Daily claw machine Details Paid grab 599 → 499 · Site load" in log
    assert "Claw pool changed Daily claw machine · prize pool" in log
    assert "70 / 25 / 5 → 65 / 25 / 10 · Rarer" in log


async def _page(admin_provider: IdentityProvider, base: str, path: str) -> str:
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        return (await sign_in(client, base, next=path)).text
