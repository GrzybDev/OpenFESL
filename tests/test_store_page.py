import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from identity import form_token
from store import CHEER, ITEMS, ROYAL_SOLDIER, grant, iconed, icons, stock, without_rotation
from support import Products, visible

from openfesl.core.store import Bundle

pytestmark = pytest.mark.anyio

# The invented catalogue's products: the Royal coat for good and for 30 days, the National
# tunic, the Royal pet and 25 bandages, and the cheer for good where it is stocked.
COAT, COAT_MONTH, TUNIC, PET, BANDAGES, CHEER_FOR_GOOD = 1, 2, 3, 5, 6, 21
PAGE = "/bfheroes/store/buy/{}"


async def shown(client: httpx2.AsyncClient, base: str, number: int, **chosen: int) -> str:
    """Return the text a store page shows, for a hero and a price if chosen."""
    page = await client.get(f"{base}{PAGE.format(number)}", params=chosen)
    assert page.status_code == 200, page.text
    return visible(page.content)


async def pictured(client: httpx2.AsyncClient, base: str, number: int) -> list[str]:
    """Return the names of the imported icons that a store page shows, in its order."""
    return icons((await client.get(f"{base}{PAGE.format(number)}")).text)


async def confirmation(
    client: httpx2.AsyncClient, base: str, number: int, hero: int, price: int
) -> tuple[str, dict[str, str]]:
    """Send a store page's choice as the page does without scripts.

    Returns:
        The confirmation page's text, and the fields its form sends.
    """
    page = await client.get(f"{base}{PAGE.format(number)}")
    chosen = {"csrf": form_token(page.text), "hero": str(hero), "price": str(price)}
    answer = await client.post(f"{base}{PAGE.format(number)}", data=chosen)
    assert answer.status_code == 200, answer.text
    fields = dict(re.findall(r'name="(csrf|hero|price|paid|once)"\s+value="([^"]*)"', answer.text))
    return visible(answer.content), fields


async def sent(client: httpx2.AsyncClient, base: str, number: int, form: dict[str, str]) -> str:
    """Send a confirmation's form, and return the markup of the page it leads back to."""
    answer = await client.post(f"{base}{PAGE.format(number)}/confirm", data=form)
    assert answer.status_code == 303, answer.text
    return (await client.get(f"{base}{answer.headers['location']}")).text


async def bought(client: httpx2.AsyncClient, base: str, number: int, form: dict[str, str]) -> str:
    """Send a confirmation's form, and return the text of the page it leads back to."""
    return visible((await sent(client, base, number, form)).encode())


@pytest.mark.usefixtures("files", "prompt_actions")
async def test_a_store_page_shows_each_hero_what_it_holds_or_why_it_cannot_have_the_item(
    base: str, server_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await stock(server_url, tmp_path, capsys, *iconed([*ITEMS, CHEER]))
    async with httpx2.AsyncClient() as client:
        signed_out = await shown(client, base, COAT_MONTH)
        hidden = await pictured(client, base, COAT_MONTH)
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        no_hero = await shown(client, base, COAT_MONTH)
    await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    mud = await create_hero(base, "ann@example.test", "Mudlark")

    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        offered = await shown(client, base, COAT_MONTH)
        pictures = await pictured(client, base, COAT_MONTH)
        other = await shown(client, base, COAT_MONTH, hero=mud)
        # Every hero starts with bandages, to which bought uses add, and the game grants it the
        # cheer.
        bandages = await shown(client, base, BANDAGES)
        cheer = await shown(client, base, CHEER_FOR_GOOD)
        polish = visible((await client.get(f"{base}/pl{PAGE.format(COAT_MONTH)}")).content)

    assert "Sign in to buy" in signed_out
    assert "Requires Royal Army, any class, any level" in signed_out
    # The game's art is shown to signed-in players: the coat's icon on its tile and in its row,
    # where a visitor gets OpenFESL's tiles.
    assert (hidden, pictures) == ([], ["item3", "item3"])
    assert "the account has no hero yet" in no_hero and "4 slots are free" in no_hero
    assert "Brassbuckle Soldier · level 1 Not owned" in offered
    assert "Mudlark Commando · level 1 Royal Army only" in offered
    assert "30 days 1,300 Funds Permanent 2,600 Funds 100 short" in offered
    assert "Brassbuckle gets Invented Coat for 30 days, until" in offered
    assert "Price 1,300 Funds Funds now 2,500 After 1,200" in offered
    assert "About A long coat with brass buttons. Keeps the rain out." in offered
    assert (
        "Invented Coat is for the Royal Army only, and Mudlark is in the National Army. "
        "Another hero of the account can get it."
    ) in other
    assert "Brassbuckle Soldier · level 1 10 uses left" in bandages
    assert "Brassbuckle gets 25 uses of Invented Bandage: 10 + 25 = 35." in bandages
    assert "Brassbuckle Soldier · level 1 Owns it permanently" in cheer
    assert "Brassbuckle owns Invented Cheer permanently. There is nothing more to buy" in cheer
    assert "Kup dla jednego bohatera" in polish and "Tylko Royal Army" in polish


@pytest.mark.usefixtures("prompt_actions")
async def test_a_hero_buys_an_item_on_its_page_and_refused_purchases_spend_nothing(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, [*ITEMS, CHEER])
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)

    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        review, form = await confirmation(client, base, COAT_MONTH, brass, COAT_MONTH)
        # The same purchase confirmed in another window before the first one buys.
        _, other = await confirmation(client, base, COAT_MONTH, brass, COAT_MONTH)
        done = await bought(client, base, COAT_MONTH, form)
        # The confirmation sent again, as Back and sending it do, buys nothing more.
        again = await bought(client, base, COAT_MONTH, form)
        # A second month is more than the account has left: the page says so, and a purchase
        # confirmed before is refused when it arrives.
        short = await bought(client, base, COAT_MONTH, other)
        # A price that changed since the page showed it, and a form from another site.
        stale = await bought(client, base, PET, form | {"price": str(PET), "paid": "800"})
        forged = await bought(client, base, PET, form | {"csrf": "forged"})
        # A confirmation without its one-time token buys nothing.
        sent = {name: value for name, value in form.items() if name != "once"}
        tokenless = await bought(client, base, PET, sent | {"price": str(PET), "paid": "899"})
        # The cheer, which the game grants every hero.
        cheer = {"price": str(CHEER_FOR_GOOD), "paid": "1679"}
        granted = await bought(client, base, CHEER_FOR_GOOD, form | cheer)
        # The pet taken out of use, as an admin's revocation or the game's status write leaves it.
        await execute(
            database_url,
            "INSERT INTO entitlement (account_id, persona_id, tag, status) SELECT account_id, id,"
            " '521-unlm', 'DISABLED' FROM persona WHERE id = :hero",
            hero=brass,
        )
        pet = await shown(client, base, PET)
        taken = await bought(client, base, PET, form | {"price": str(PET), "paid": "899"})

    assert "Confirm the purchase Item Invented Coat Hero Brassbuckle Time 30 days, until" in review
    assert "Price 1,300 Funds Funds now 2,500 Funds after 1,200" in review
    end = datetime.now(UTC) + timedelta(days=30)
    day = f"{end.day} {end:%b %Y}"
    assert f"Invented Coat bought for Brassbuckle: 30 days, until {day}. 1,300 Funds spent." in done
    assert "Brassbuckle Soldier · level 1 Owns it · 30 days left" in done
    assert "The account has 1,200 Funds; this costs 1,300. How Funds are earned" in done
    assert (
        "The purchase of Invented Coat for Brassbuckle was made already; sending it again bought "
        "nothing more. No more Funds were spent."
    ) in again
    assert (
        "Nothing was bought: the account had 1,200 Funds when the purchase arrived, and "
        "Invented Coat costs 1,300. No Funds were spent."
    ) in short
    assert (
        "Nothing was bought: the price of Invented Monkey changed to 899 Funds. No Funds were "
        "spent; the page shows the new price."
    ) in stale
    for expired in (forged, tokenless):
        assert "Nothing was bought: the form had expired. Nothing was spent." in expired
    assert (
        "Nothing was bought: Brassbuckle owns Invented Cheer permanently. No Funds were spent."
    ) in granted
    assert "Brassbuckle Soldier · level 1 Cannot get it again" in pet
    assert "Brassbuckle cannot get Invented Monkey again: it was taken out of use." in pet
    assert (
        "Nothing was bought: Brassbuckle cannot get Invented Monkey again, as it was taken out "
        "of use. No Funds were spent."
    ) in taken
    assert await select_value(database_url, "SELECT count(*) FROM purchase") == 1
    assert "1,200 Funds" in forged


@pytest.mark.usefixtures("files", "prompt_actions")
async def test_hot_deals_and_random_boxes_sell_on_their_pages_while_they_run(
    base: str,
    server_url: str,
    database_url: str,
    products: Products,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, *iconed(ITEMS))
    await without_rotation(database_url)
    now = datetime.now(UTC)
    royal_set = Bundle(
        "WEST_Custom_Bundle_Invented",
        "WEST_Custom_Bundle_Invented_HotDealsDesc",
        "_PF",
        2000,
        original_price=3675,
        faction="royal",
        ends=now + timedelta(days=1, hours=2),
        members=(COAT, PET, BANDAGES),
    )
    deal = await products.add_bundle("bfheroes", royal_set)
    ended = await products.add_bundle(
        "bfheroes", Bundle("Old Set", None, "_PF", 10, ends=now, members=(TUNIC,))
    )
    box = await select_value(
        database_url, "SELECT id FROM store_box WHERE key = 'royal-soldier-supply-drop'"
    )
    assert isinstance(box, int)
    drop = await products.add_bundle(
        "bfheroes", Bundle("Royal Soldier Supply Drop", None, "_PF", 629, faction="royal", box=box)
    )
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Mudlark")

    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        offered = await shown(client, base, deal)
        set_icons = await pictured(client, base, deal)
        _, form = await confirmation(client, base, deal, brass, deal)
        dealt = await bought(client, base, deal, form)
        gone = await shown(client, base, ended)
        late = await bought(client, base, ended, form | {"price": str(ended), "paid": "10"})
        await grant(database_url, brass, "_PF", 1000)
        boxed = await shown(client, base, drop)
        box_icons = await pictured(client, base, drop)
        _, form = await confirmation(client, base, drop, brass, drop)
        revealed = await sent(client, base, drop, form)
        opened = visible(revealed.encode())
        again = await shown(client, base, drop)

    assert (
        "Hot deal: 3 items for one hero at one price. The price is 45% lower than usual. Sold until"
    ) in offered
    assert "-45% 45% off Deal ends in 1 d 01:" in offered
    assert "Invented Coat Permanent Invented Monkey Permanent Invented Bandage 25 uses" in offered
    # A tile and a row for each of the set's items, and a row for each item the box may give.
    assert set_icons == ["item3", "item521", "item2005"] * 2
    assert box_icons == ["item521"]
    assert "About A coat, a pet, bandages." in offered
    assert "Mudlark Commando · level 1 Royal Army only" in offered
    assert "Brassbuckle gets the 3 items of Invented Set as listed." in offered
    assert "Invented Set bought for Brassbuckle. 2,000 Funds spent." in dealt
    assert "This deal ended on" in gone and "The deal has ended." in gone
    assert "Nothing was bought: Old Set ended on" in late
    assert "Invented Monkey Permanent chance 100%" in boxed
    assert (
        "Each box gives one of these items, worth at least the box's price of 629 Funds." in boxed
    )
    # The hero owns the deal's pet for good, so the box gives its tonics instead, which rise with
    # their icon above the box's rows.
    assert icons(revealed) == ["item2153", "item521"]
    assert (
        "Royal Soldier Supply Drop opened for Brassbuckle: Invented Tonic, 110 uses. "
        "629 Funds spent."
    ) in opened
    assert (
        "Box opened Item for Brassbuckle Invented Tonic 110 uses · worth 770 Funds; "
        "the box cost 629 Funds."
    ) in opened
    assert "Box opened" not in again
    assert (
        await select_value(database_url, "SELECT string_agg(kind, ' ' ORDER BY id) FROM purchase")
        == "bundle box"
    )
