import json
from pathlib import Path

import httpx2
import pytest
from accounts import select_value
from identity import IdentityProvider, form_token, sign_in
from store import deals as offered
from store import stock
from support import form_fields, visible
from test_admin_heroes import KEEPER, WARDEN

from openfesl.core.games import Game

pytestmark = pytest.mark.anyio


async def setting(database_url: str, key: str) -> object:
    """Return a setting's value, or None while the game data's default holds."""
    return await select_value(database_url, "SELECT value FROM setting WHERE key = :key", key=key)


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_switch_the_rotation_and_change_its_sections_from_their_next_step(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    deals = f"{base}/admin/store/bfheroes/deals"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next="/admin/store/bfheroes/deals")).content)
        assert "Hot-deal rotation Every section runs on its schedule" in shown
        assert "Deal of the Day Every day Runs" in shown
        assert "New sets Every 7 days Runs Size 6 per faction 50\u201350% off" in shown
        assert "Event boxes In its offers' windows Runs" in shown
        assert "Hot deals are changed with the admin role." in shown
        token = form_token((await client.get(deals)).text)
        refused = await client.post(f"{deals}/sections/sale", data={"csrf": token})
        assert refused.status_code == 403
        previewed = visible((await client.get(deals, params={"preview": "1"})).content)
        assert "Deal of the Day Every day Runs" in previewed and "Comes in" in previewed
        assert "Hide the preview" in previewed and "Preview the next step" in shown
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next="/admin/store/bfheroes/deals")).text)
        wide = {"csrf": token, "enabled": "1", "size": "2", "low": "60", "high": "30", "gap": "42"}
        backwards = await client.post(f"{deals}/sections/sale", data=wide)
        assert backwards.status_code == 400
        assert "The lowest discount is above the highest." in visible(backwards.content)
        changed = wide | {"enabled": "", "low": "20", "high": "40"}
        saved = await client.post(f"{deals}/sections/sale", data=changed, follow_redirects=True)
        assert "Saved On sale; it changes at its next step." in visible(saved.content)
        assert "On sale Every 7 days · lasts 3 days Off" in visible(saved.content)
        assert await setting(database_url, "bfheroes.deals.sections.sale.enabled") == "off"
        assert await setting(database_url, "bfheroes.deals.sections.sale.discount") == "[20, 40]"
        # A value back at the game data's leaves the setting.
        back = changed | {"low": "25", "high": "50"}
        await client.post(f"{deals}/sections/sale", data=back)
        assert await setting(database_url, "bfheroes.deals.sections.sale.discount") is None

        assert await offered(server_url, "en")
        off = await client.post(f"{deals}/rotation", data={"csrf": token}, follow_redirects=True)
        assert "Switched the hot-deal rotation off" in visible(off.content)
        assert await setting(database_url, "bfheroes.deals.enabled") == "off"
        # As the page says, the game then lists no hot deal at all.
        assert not await offered(server_url, "en")
        await client.post(f"{deals}/rotation", data={"csrf": token, "enabled": "1"})
        assert await setting(database_url, "bfheroes.deals.enabled") is None

        log = visible((await client.get(f"{base}/admin/audit")).content)
        assert "Rotation switched on Hot-deal rotation" in log
        assert "Rotation switched off Hot-deal rotation" in log
        assert (
            "Section changed On sale Details Runs → Off; discount 25\u201350% off → 20\u201340% off"
            in log
        )


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_pin_and_exclude_the_offers_of_a_sections_pool(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> None:
    await stock(server_url, tmp_path, capsys)
    sections = f"{base}/admin/store/bfheroes/deals/sections"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        fields = form_fields(
            (await sign_in(client, base, next=f"{sections[len(base) :]}/new")).text
        )
        changed = {"pool-0": ["pin"], "pool-1": ["out"]}
        saved = await client.post(f"{sections}/new", data=fields | changed, follow_redirects=True)
        shown = visible((await client.get(f"{sections}/new")).content)
        daily = visible((await client.get(f"{sections}/daily")).content)
        popular = visible((await client.get(f"{sections}/popular")).content)
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert bfheroes.deals is not None
    first, second = (offer.name for offer in bfheroes.deals.sets[:2])
    assert "Saved New sets; it changes at its next step." in visible(saved.content)
    pinned = await setting(database_url, "bfheroes.deals.sections.new.pinned")
    excluded = await setting(database_url, "bfheroes.deals.sections.new.excluded")
    assert (pinned, excluded) == (json.dumps([first]), json.dumps([second]))
    count = len(bfheroes.deals.sets)
    assert f"Pool {count - 2} included · 1 pinned · 1 excluded" in shown
    assert "Pinned offers come first at each step, also within the repeat gap" in shown
    # A section of items has no pool, and one that sells all its offers no pins.
    assert "This section puts items together by their slots, so it has no pool of offers." in daily
    assert "The section sells every offer of its pool" in popular
    assert f"Section changed New sets Details pinned {first}; excluded {second}" in log
