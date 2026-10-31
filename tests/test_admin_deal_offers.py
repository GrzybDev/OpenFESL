from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from accounts import select_value
from identity import IdentityProvider, form_token, sign_in
from living import setting
from playwright.async_api import Browser
from store import ITEMS, attribute, deals, item, stock, without_rotation
from support import form_fields, visible
from test_admin_heroes import KEEPER, WARDEN

pytestmark = pytest.mark.anyio

OFFERS = "/admin/store/bfheroes/deals/offers"
# The invented catalogue with a second boost, which with the first makes the game's boost pair.
BOOSTS = [*ITEMS, item(9005, "convenience")]


async def product(database_url: str, item_id: int, days: int | None = None) -> int:
    """Return the number of an item's product in Funds, for good or rented for some days."""
    number = await select_value(
        database_url,
        "SELECT id FROM store_product WHERE item_id = :item AND currency = '_PF'"
        " AND days IS NOT DISTINCT FROM CAST(:days AS integer)",
        item=item_id,
        days=days,
    )
    assert isinstance(number, int)
    return number


def written(moment: datetime) -> str:
    return f"{moment:%Y-%m-%dT%H:%M}"


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_add_and_change_offers_that_hot_deals_sell_at_once(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    coat, tunic, pet = [await product(database_url, number) for number in (3, 4, 521)]
    worth = await select_value(
        database_url,
        "SELECT sum(price)::integer FROM store_product WHERE id IN (:coat, :pet)",
        coat=coat,
        pet=pet,
    )
    assert isinstance(worth, int)
    now = datetime.now(UTC)
    new = f"{base}{OFFERS}/new"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=OFFERS)
        fields = form_fields((await client.get(new)).text)
        # Without scripts, Find and Add answer the form as written and save nothing.
        found = visible(
            (await client.post(new, data=fields | {"q": ["Coat"], "find": ["1"]})).content
        )
        added = await client.post(new, data=fields | {"add": [str(coat)]})
        added = await client.post(new, data=form_fields(added.text) | {"add": [str(pet)]})
        context = await edge.new_context(locale="en-GB")
        for name, value in client.cookies.items():
            await context.add_cookies([{"name": name, "value": value, "url": base}])
        # With scripts, Add answers the form too while saving waits for the English name.
        scripted = await context.new_page()
        await scripted.goto(new)
        await scripted.locator("#offer-q").fill("Coat")
        add = scripted.locator(f"button[name=add][value='{coat}']")
        await add.wait_for()
        save = scripted.locator(".reward-save button[type=submit]")
        waiting = await save.get_attribute("aria-disabled")
        async with scripted.expect_navigation():
            await add.click()
        held = await scripted.locator("input[name=item]").count()
        await context.close()
        kit = form_fields(added.text) | {
            "name-en": ["Invented Kit"],
            "name-pl": ["Wymyślony komplet"],
            "amount": ["30"],
        }
        on_sale = await client.post(new, data=kit | {"section": ["sale"], "amount": ["70"]})
        backwards = await client.post(new, data=kit | {"ends": [written(now - timedelta(days=1))]})
        mixed = await client.post(new, data=form_fields(added.text) | {"add": [str(tunic)]})
        unfit = await client.post(new, data=form_fields(mixed.text) | {"name-en": ["Mixed"]})
        saved = await client.post(new, data=kit | {"section": ["popular"]}, follow_redirects=True)
        listed = visible((await client.get(f"{base}{OFFERS}")).content)
        polish, german = await deals(server_url, "pl"), await deals(server_url, "de")
        page = visible((await client.get(f"{base}/pl/bfheroes/store/hot-deals")).content)
        rotation = visible((await client.get(f"{base}/admin/store/bfheroes/deals")).content)
        number = await select_value(
            database_url, "SELECT id FROM store_bundle WHERE name = 'Invented Kit'"
        )
        # Its editor shows it as saved, and a change applies at once.
        offer = f"{base}{OFFERS}/{number}"
        await client.post(
            offer, data=form_fields((await client.get(offer)).text) | {"amount": ["50"]}
        )
        changed = await deals(server_url, "en")
        # Its section switched off hides it, and a new offer of the section is saved hidden.
        await setting(database_url, "bfheroes.deals.sections.popular.enabled", "off")
        hidden = await deals(server_url, "en")
        second = await client.post(
            new,
            data=kit | {"name-en": ["Second Kit"], "section": ["popular"]},
            follow_redirects=True,
        )
        editor = visible((await client.get(offer)).content)
        log = visible((await client.get(f"{base}/admin/audit")).content)

    assert "Invented Coat item 3" in found and "Add · Permanent" in found
    assert waiting == "true" and held == 1
    assert "Sum of list prices " + f"{worth:,} Funds" in visible(added.content)
    assert "On sale takes discounts from 25 to 50%; 70% is outside that range." in visible(
        on_sale.content
    )
    assert on_sale.status_code == backwards.status_code == unfit.status_code == 400
    assert "The offer ends before it starts" in visible(backwards.content)
    assert "No hero may have all these items" in visible(unfit.content)
    assert "Saved Invented Kit; hot deals list it from its start until" in visible(saved.content)
    price = (worth * 70 // 100 + 1) // 100 * 100 - 1
    assert (
        f"Invented Kit 2 items · own text Section Popular Price {price:,} Funds 30% off {worth:,}"
        in listed
    )
    # The game lists it at once, named in its language or else English.
    assert [attribute(deal, "Product Name") for deal in polish] == ["Wymyślony komplet"]
    assert [attribute(deal, "Product Name") for deal in german] == ["Invented Kit"]
    kit_deal = polish[0].find("PricePoints/pricePoint")
    assert kit_deal is not None and kit_deal.get("price") == str(price)
    assert "Wymyślony komplet" in page
    assert "Rotation off: hot deals show only admin offers (1 live now)" in rotation
    halved = changed[0].find("PricePoints/pricePoint")
    assert halved is not None and halved.get("price") == str((worth // 2 + 1) // 100 * 100 - 1)
    assert not hidden
    assert "Saved Second Kit; its section is off, so hot deals do not list it." in visible(
        second.content
    )
    assert visible(second.content).count("Hidden · section off") == 2
    assert "Invented Kit bundle" in editor and "Hidden · section off Items" in editor
    assert "Offer added Invented Kit Details Popular · " in log
    assert "Offer saved Invented Kit Details Popular · " in log


@pytest.mark.usefixtures("prompt_actions")
async def test_an_offer_of_a_game_bundles_items_may_take_the_bundles_texts(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, BOOSTS)
    await without_rotation(database_url)
    boosts = [await product(database_url, number, 30) for number in (9000, 9005)]
    new = f"{base}{OFFERS}/new"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next=OFFERS)
        fields = form_fields((await client.get(new)).text)
        one = await client.post(new, data=fields | {"add": [str(boosts[0])]})
        pair = await client.post(new, data=form_fields(one.text) | {"add": [str(boosts[1])]})
        imported = form_fields(pair.text) | {"text": ["imported"], "section": ["popular"]}
        alone = await client.post(
            new, data=form_fields(one.text) | {"text": ["imported"], "amount": ["30"]}
        )
        # The price as the panel writes it, its digits grouped.
        await client.post(new, data=imported | {"pricing": ["price"], "amount": ["9,999"]})
        polish, german = await deals(server_url, "pl"), await deals(server_url, "de")
        listed = visible((await client.get(f"{base}{OFFERS}")).content)

    assert "No game bundle has these items, so the offer needs own text." in visible(one.content)
    assert "No game bundle has these items" not in visible(pair.content)
    assert alone.status_code == 400
    assert "No game bundle has these items" in visible(alone.content)
    # The game's texts name it in every language the game has.
    assert [attribute(deal, "Product Name") for deal in polish] == ["Wymyślona para dopalaczy"]
    assert [attribute(deal, "Product Name") for deal in german] == ["Invented Boost Pair"]
    assert "Invented Boost Pair 2 items · imported text Section Popular Price 9,999" in listed


@pytest.mark.usefixtures("prompt_actions")
async def test_moderators_read_offers_and_only_admins_change_them(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        shown = visible((await sign_in(client, base, next=OFFERS)).content)
        token = form_token((await client.get(f"{base}{OFFERS}")).text)
        new = await client.get(f"{base}{OFFERS}/new")
        refused = await client.post(f"{base}{OFFERS}/new", data={"csrf": token})
    assert "No admin offers." in shown and "Hot deals are changed with the admin role." in shown
    assert new.status_code == refused.status_code == 403
