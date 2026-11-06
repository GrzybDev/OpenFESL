from datetime import UTC, datetime

import httpx2
import pytest
from accounts import PASSWORD, post_sign_in, select_value
from heroes import CREATOR, create_hero
from identity import IdentityProvider, form_token, sign_in
from living import freeze, setting
from store import ROYAL_SOLDIER
from support import once_token, visible
from test_admin_heroes import KEEPER, WARDEN

from openfesl.core.games import Game
from openfesl.web.admin.store_account import sale_rows
from openfesl.web.store_texts import StoreTexts

pytestmark = pytest.mark.anyio

OCTOBER_SALE = datetime(2026, 10, 2, 16, 45, tzinfo=UTC)
SLOTS = "/bfheroes/user/buyCharacterSlots"


async def test_the_sales_take_their_share_off_on_their_days_and_end_at_midnight(
    bfheroes: Game,
) -> None:
    assert bfheroes.heroes is not None
    rules = bfheroes.heroes
    outside = rules.at(datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    assert (outside.slot_price, outside.slot_sale) == (2499, None)
    october = rules.at(OCTOBER_SALE)
    assert (october.slot_price, october.rename_price, october.rename_sale) == (1749, 5899, None)
    assert october.slot_sale is not None
    assert october.slot_sale.ends == datetime(2026, 10, 6, tzinfo=UTC)
    # Two sales at once, of a slot and of a name.
    both = rules.at(datetime(2026, 11, 16, tzinfo=UTC))
    assert (both.slot_price, both.rename_price) == (1249, 2949)
    assert rules.at(datetime(2026, 10, 6, tzinfo=UTC)).slot_sale is None
    off = rules.model_copy(update={"sales_run": False})
    assert off.at(OCTOBER_SALE).slot_price == 2499


async def test_the_panel_says_of_each_sale_whether_it_runs_or_how_long_until_it_does(
    bfheroes: Game,
) -> None:
    assert bfheroes.heroes is not None
    texts = StoreTexts(bfheroes, "en", OCTOBER_SALE)
    rows = sale_rows(bfheroes.heroes, texts, OCTOBER_SALE)
    assert [(row.dates, row.slot, row.rename, row.price) for row in rows] == [
        ("2\N{EN DASH}6 Jul", "50%", "—", "1,249"),
        ("6\N{EN DASH}10 Jul", "—", "50%", "2,949"),
        ("1\N{EN DASH}5 Oct", "30%", "—", "1,749"),
        ("13\N{EN DASH}17 Nov", "50%", "—", "1,249"),
        ("15\N{EN DASH}19 Nov", "—", "50%", "2,949"),
    ]
    assert [row.state for row in rows] == [
        "Next year",
        "Next year",
        "Running · ends in 3 d 07:15:00",
        "In 42 days",
        "In 44 days",
    ]
    assert [row.running for row in rows] == [False, False, True, False, False]


@pytest.mark.hero_sales
@pytest.mark.usefixtures("prompt_actions")
async def test_a_hero_slot_on_sale_shows_its_price_everywhere_and_sells_at_it_until_it_ends(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch, OCTOBER_SALE)
    for name in ("Brassbuckle", "Tin_Tankard", "Pip-Lantern", "Mossgrove"):
        await create_hero(base, "ann@example.test", name, **ROYAL_SOLDIER)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        heroes = visible((await client.get(f"{base}/bfheroes/heroes")).content)
        creator = visible((await client.get(f"{base}{CREATOR}")).content)
        deals = visible((await client.get(f"{base}/bfheroes/store/hot-deals")).content)
        page = await client.get(f"{base}{SLOTS}")
        shown = visible(page.content)
        form = {
            "csrf": form_token(page.text),
            "next": CREATOR,
            "once": once_token(page.text),
            "price": "1749",
        }
        await client.post(f"{base}{SLOTS}", data=form)
        balance = await select_value(database_url, "SELECT balance FROM wallet")
        # The sale ends with a form of its price still open.
        freeze(monkeypatch, datetime(2026, 10, 6, tzinfo=UTC))
        late = await client.get(f"{base}{SLOTS}")
        form = {
            "csrf": form_token(late.text),
            "next": CREATOR,
            "once": once_token(late.text),
            "price": "1749",
        }
        await client.post(f"{base}{SLOTS}", data=form)
        ended = visible((await client.get(f"{base}{CREATOR}")).content)

    assert "Each further slot costs 1,749 Funds until 6 October, then 2,499." in heroes
    assert "Another slot costs 1,749 Funds until 6 October, then 2,499 and stays" in heroes
    assert "Buy a hero slot was 2,499 1,749 Funds" in heroes
    assert "A slot costs 1,749 Funds until 6 October, then 2,499 and stays" in creator
    assert "Price was 2,499 1,749 Funds" in creator
    assert "Hero slot -30% 30% off" in deals and "was 2,499 1,749 Funds" in deals
    assert "One more hero on the account" in deals and "Buy a hero slot" in deals
    assert "Price was 2,499 1,749 Funds -30% 30% off Sale 30% off" in shown
    assert "Funds after 751" in shown and "Buy for 1,749 Funds" in shown
    assert balance == 751
    assert "the price of a slot changed to 2,499 Funds. Nothing was spent." in ended
    ledger = await select_value(
        database_url, "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY id) FROM wallet_entry"
    )
    assert ledger == "allowance:2500 hero-slot:-1749"


@pytest.mark.hero_sales
@pytest.mark.usefixtures("prompt_actions")
async def test_a_rename_on_sale_shows_its_price_on_the_hero_and_in_the_hot_deals(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch, datetime(2026, 11, 16, 12, tzinfo=UTC))
    # An allowance that pays the sale's price, not the usual one.
    await setting(database_url, "bfheroes.currencies._PF.allowance", "3000")
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        heroes = visible((await client.get(f"{base}/bfheroes/heroes")).content)
        page = await client.get(f"{base}/bfheroes/heroes/{hero}/rename")
        rename = visible(page.content)
        deals = visible((await client.get(f"{base}/bfheroes/store/hot-deals")).content)
        form = {"csrf": form_token(page.text), "price": "2949", "name": "Copper_Kettle"}
        await client.post(f"{base}/bfheroes/heroes/{hero}/rename", data=form)
    ledger = await select_value(
        database_url, "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY id) FROM wallet_entry"
    )
    assert ledger == "allowance:3000 rename:-2949"
    assert "Rename Brassbuckle was 5,899 2,949 Funds" in heroes
    assert "Price was 5,899 2,949 Funds -50% 50% off Sale 50% off" in rename
    assert "Rename for 2,949 Funds" in rename
    assert "Hero rename -50% 50% off" in deals and "Hero slot -50% 50% off" in deals


@pytest.mark.hero_sales
@pytest.mark.usefixtures("prompt_actions")
async def test_the_admin_switches_the_sales_off_and_on_and_moderators_only_read_them(
    admin_provider: IdentityProvider,
    base: str,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    freeze(monkeypatch, OCTOBER_SALE)
    page = f"{base}/admin/store/bfheroes/account"
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = await sign_in(client, base, next="/admin/store/bfheroes/account")
        assert "Sales of hero slots and renames" in visible(shown.content)
        assert 'name="sales"' not in shown.text
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        shown = await sign_in(client, base, next="/admin/store/bfheroes/account")
        values = {
            "csrf": form_token(shown.text),
            "slot": "2499",
            "rename": "5899",
            "key": "499",
            "allowance-_PF": "2500",
            "allowance-_DV": "0",
            "credits-_DV": "1000",
            "chests": "5",
        }
        # A refused form keeps the switch as it was sent.
        refused = await client.post(page, data=values | {"chests": "0"})
        assert refused.status_code == 400
        assert 'name="sales" value="1" class="check"' in " ".join(refused.text.split())
        await client.post(page, data=values)
        running = "SELECT value FROM setting WHERE key = 'bfheroes.heroes.sales_run'"
        assert await select_value(database_url, running) == "false"
        await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
        async with httpx2.AsyncClient() as player:
            await post_sign_in(player, base, "ann@example.test", PASSWORD)
            usual = visible((await player.get(f"{base}/bfheroes/heroes")).content)
        await client.post(page, data=values | {"sales": "1"})
        assert await select_value(database_url, running) is None
        log = visible((await client.get(f"{base}/admin/audit")).content)
    assert "Each further slot costs 2,499 Funds." in usual
    assert "Sales of hero slots and renames Details Switched off" in log
    assert "Sales of hero slots and renames Details Switched on" in log
