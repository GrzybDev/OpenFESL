from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, post_sign_in, select_value
from heroes import create_hero
from identity import IdentityProvider, form_token, sign_in
from store import stock
from support import once_token, visible
from test_admin_heroes import KEEPER, WARDEN

pytestmark = pytest.mark.anyio

# The values of the game data, as the form sends them unchanged.
VALUES = {
    "slot": "2499",
    "rename": "5899",
    "key": "499",
    "allowance-_PF": "2500",
    "allowance-_DV": "0",
    "credits-_DV": "1000",
    "chests": "5",
}
SETTINGS = "SELECT string_agg(key || '=' || value, ' ' ORDER BY key) FROM setting"


@pytest.mark.usefixtures("prompt_actions")
async def test_account_prices_and_allowances_change_on_the_panel_and_apply_at_once(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    hero = await create_hero(base, "ann@example.test", "Brassbuckle")
    page = f"{base}/admin/store/bfheroes/account"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next="/admin/store/bfheroes/account")).content)
        assert (
            "Account prices, allowances and per-round caps All default Setting Value Unit Game"
            " data State Hero slot A further slot beyond the 4 free ones 2,499 Unit Funds Game"
            " data 2,499 Default" in shown
        )
        assert "Valor Points per round Most that servers credit an account within 10 minutes" in (
            shown
        )
        assert "Prices are changed with the admin role." in shown and "Save" not in shown
        token = form_token((await client.get(page)).text)
        assert (await client.post(page, data={"csrf": token, **VALUES})).status_code == 403
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next="/admin/store/bfheroes/account")).text)
        refused = await client.post(page, data={"csrf": token, **VALUES, "chests": "0"})
        assert refused.status_code == 400
        assert "This value is from 1 to 2,147,483,647; 0 is not." in visible(refused.content)
        # What a script sends while a value is typed: its state follows it.
        typed = visible((await client.get(page, params=VALUES | {"slot": "1,800"})).content)
        assert "per-round caps 1 changed" in typed and "2,499 Changed Reset" in typed
        # Reset puts the value back in the form and saves nothing.
        sent = {"csrf": token, **VALUES, "slot": "1800", "reset": "slot"}
        reset = await client.post(page, data=sent)
        assert 'value="2499"' in reset.text and "All default" in visible(reset.content)
        assert await select_value(database_url, SETTINGS) is None

        changed = VALUES | {
            "slot": "1,800",
            "rename": "1,600",
            "key": "599",
            "allowance-_PF": "3000",
        }
        saved = await client.post(
            page, data={"csrf": token, **changed, "allowance-_DV": "500"}, follow_redirects=True
        )
        assert "Saved the account prices and limits. Values changed: 5." in visible(saved.content)
        assert await select_value(database_url, SETTINGS) == (
            "bfheroes.chests.key_price=599 bfheroes.currencies._DV.allowance=500"
            " bfheroes.currencies._PF.allowance=3000 bfheroes.heroes.rename_price=1600"
            " bfheroes.heroes.slot_price=1800"
        )
        log = visible((await client.get(f"{base}/admin/audit")).content)
        assert "Account price or limit changed Hero slot Details 2,499 → 1,800 Funds" in log
        assert "Starting allowance · Valor Points Details 0 → 500 Valor Points" in log

        # The prices, the allowances of wallets not opened yet and the key apply at once.
        async with httpx2.AsyncClient() as ann:
            await post_sign_in(ann, base, "ann@example.test", PASSWORD)
            heroes = visible((await ann.get(f"{base}/bfheroes/heroes")).content)
            chests = visible((await ann.get(f"{base}/bfheroes/treasure-chests")).content)
            # Forms shown at the game data's prices before the change buy nothing.
            shown = (await ann.get(f"{base}/bfheroes/user/buyCharacterSlots")).text
            old = {"csrf": form_token(shown), "once": once_token(shown)}
            await ann.post(f"{base}/bfheroes/user/buyCharacterSlots", data=old | {"price": "2499"})
            slot = visible((await ann.get(f"{base}/bfheroes/heroes")).content)
            renaming = old | {"name": "Copper_Kettle", "price": "5899"}
            await ann.post(f"{base}/bfheroes/heroes/{hero}/rename", data=renaming)
            renamed = visible((await ann.get(f"{base}/bfheroes/heroes")).content)
        assert "Heroes 1 3,000 Funds 500 Valor Points" in heroes
        assert "Each further slot costs 1,800 Funds." in heroes
        assert "1 key 599 Funds 3 keys 1,797 Funds" in chests
        assert "No hero slot was added: the price of a slot changed to 1,800 Funds." in slot
        assert (
            "Brassbuckle was not renamed: the price of a rename changed to 1,600 Funds." in renamed
        )
        assert await select_value(database_url, "SELECT count(*) FROM wallet") == 0

        back = await client.post(page, data={"csrf": token, **changed, "allowance-_DV": "500"})
        assert back.status_code == 303
        unchanged = visible((await client.get(page)).content)
        assert "Nothing changed in the account prices and limits." in unchanged
        await client.post(page, data={"csrf": token, **VALUES})
        assert await select_value(database_url, SETTINGS) is None
        polish = visible((await client.get(f"{base}/pl/admin/store/bfheroes/account")).content)
        assert "Wszystkie domyślne" in polish and "w ciągu 10 minut;" in polish
