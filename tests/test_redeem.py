import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import anyio
import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import create_hero
from identity import IdentityProvider, form_token, sign_in
from living import NATIONAL, ROYAL, import_catalogue, worn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER
from support import visible
from test_admin_keys import KEEPER

from openfesl.core.gift_codes import CodeGrant
from openfesl.core.inventory import COUNTED, RENTED, Held, Ownership
from openfesl.core.purchases import Purchase
from openfesl.core.wallets import Wallet
from openfesl.storage import gift_codes, purchases
from openfesl.storage.entitlements import add_item
from openfesl.storage.gift_codes import SqlGiftCodeStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.purchases import SqlPurchaseStore

pytestmark = pytest.mark.anyio
REDEEM = "/bfheroes/store/redeem"
# A Royal hat, a National commando's rifle from level 10 and a tonic sold as uses.
ITEMS = {
    1081: worn("Invented Mask", ROYAL, "0"),
    3042: ("Invented Longrifle", "weapon", {"faction": NATIONAL, "kits": "1", "minLevel": "10"}),
    2153: ("Invented Tonic", "ability", {"consumable": "true"}),
}
CRATE = "7KQ4-M2XC-9HTV-RB3E"


async def code(database_url: str, written: str, gift: dict[str, object], **terms: object) -> None:
    """Add a code that gives a gift, as the admin's New code makes one."""
    await execute(
        database_url,
        "INSERT INTO product_key"
        " (code, game, gift, labels, max_uses, once_per_account, expires_at, enabled)"
        " VALUES (:code, 'bfheroes', CAST(:gift AS jsonb), CAST(:labels AS jsonb), :max_uses,"
        " true, :expires, true)",
        code=written,
        gift=json.dumps(gift),
        labels=json.dumps({"en": "Invented crate"}),
        max_uses=terms.get("max_uses"),
        expires=terms.get("expires"),
    )


@pytest.mark.usefixtures("prompt_actions")
async def test_a_linked_code_survives_the_sign_in_and_gives_its_gift_once_to_a_hero_it_fits(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, ITEMS)
    gift = {"items": ["1081 7d", "2153 x25"], "funds": 500, "keys": 2}
    await code(database_url, CRATE, gift, expires=datetime(2099, 11, 2, 8, tzinfo=UTC))
    royal = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await create_hero(base, "ann@example.test", "Tin_Tankard", faction="national")
    named = "UPDATE account SET display_name = 'Ann_Invented' WHERE nuid = 'ann@example.test'"
    await execute(database_url, named)
    linked = f"{base}{REDEEM}?key=7kq4 m2xc 9htv rb3e"
    async with httpx2.AsyncClient() as client:
        signed_out = await client.get(linked)
        link = re.search(r'href="/sign-in\?next=([^"]+)"', signed_out.text)
        assert link
        back = unquote(link.group(1))
        signed = await post_sign_in(client, base, "ann@example.test", PASSWORD, next=back)
        shown = await client.get(base + signed.headers["location"])
        token = form_token(shown.text)
        form = {"csrf": token, "key": CRATE, "hero": str(royal)}
        # An expired form shows the code again as the entry form does, not as a link's.
        stale = await client.post(f"{base}{REDEEM}", data=form | {"csrf": "expired"})
        expired = visible((await client.get(base + stale.headers["location"])).content)
        # Two redemptions at once, as from two windows, give once.
        answers = await asyncio.gather(
            *(client.post(f"{base}{REDEEM}", data=form) for _ in range(2))
        )
        done = [answer for answer in answers if answer.status_code == 303]
        raced = [answer for answer in answers if answer.status_code == 400]
        redeemed = visible((await client.get(base + done[0].headers["location"])).content)
        again = visible(
            (await client.get(f"{base}{REDEEM}", params={"key": CRATE, "from": "form"})).content
        )
        funds = await select_value(
            database_url,
            "SELECT balance FROM wallet WHERE currency = '_PF' AND account_id ="
            " (SELECT account_id FROM persona WHERE id = :hero)",
            hero=royal,
        )

    assert "Sign in to redeem" in visible(signed_out.content) and CRATE in visible(
        signed_out.content
    )
    assert parse_qs(urlparse(back).query)["key"] == ["7kq4 m2xc 9htv rb3e"]
    contents = visible(shown.content)
    assert "The code came with the link. Nothing is redeemed until Redeem is pressed." in contents
    assert "Nothing was redeemed: the form expired. The code is shown again." in expired
    assert "The code came with the link" not in expired and "Invented Mask" in expired
    assert "Once per account · until 2 Nov 2099, 08:00 UTC" in contents
    assert "Invented Mask Royal Army, any class, any level 7 days" in contents
    assert "+500" in contents and "Treasure chest keys To the account +2" in contents
    assert "Invented Mask: new, 7 days" in contents and "Invented Tonic: new, 25 uses" in contents
    assert "Invented Mask is a Royal Army item; Tin_Tankard is National Army." in contents
    assert re.search(rf'value="{royal}"[^>]*checked', shown.text)
    # The hero the items do not fit cannot be picked.
    assert re.search(r'value="\d+"[^>]*aria-disabled="true"\s+disabled', shown.text)
    assert len(done) == 1 and len(raced) == 1
    assert "Nothing was redeemed here: this account redeemed the code a moment ago" in raced[0].text
    assert f"Code {CRATE} redeemed for Brassbuckle. 500 Funds and 2 treasure chest keys" in redeemed
    assert "Brassbuckle · Invented Mask, 7 days added: 7 days left" in redeemed
    assert "Account · 500 Funds added: 3,000 Funds now" in redeemed
    assert "Account · 2 treasure chest keys added: 2 now" in redeemed
    assert "This account redeemed this code on" in again and "for Brassbuckle" in again
    assert funds == 3000
    uses = "SELECT count(*) FROM product_key_use WHERE persona_id = :hero"
    assert await select_value(database_url, uses, hero=royal) == 1
    held = "SELECT use_count FROM entitlement WHERE persona_id = :hero AND tag = '2153-lmct'"
    assert await select_value(database_url, held, hero=royal) == 25

    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        await sign_in(client, base, next="/admin/")
        page = visible((await client.get(f"{base}/admin/keys/codes/{CRATE}")).content)

    assert "Each account once · 1 so far, no limit" in page


async def test_codes_that_give_an_account_nothing_say_why_in_place(
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, ITEMS)
    await code(
        database_url,
        "J5VN-8QHC-2WKT-6ZDR",
        {"keys": 5},
        expires=datetime(2001, 9, 1, 8, tzinfo=UTC),
    )
    await code(database_url, "R4TB-9XVE-K7MC-5HGS", {"funds": 100}, max_uses=1)
    await code(database_url, "W2EH-7MPA-3KUC-9FNY", {"items": ["3042 3d"]})
    await code(database_url, "P8WD-3NZK-H6QT-2YLA", {"funds": 1000})
    await code(database_url, "K3TF-Q9WA-6NHR-8ELB", {"keys": 1})
    await execute(
        database_url, "UPDATE product_key SET enabled = false WHERE code = 'K3TF-Q9WA-6NHR-8ELB'"
    )
    commando = {"faction": "national", "class": "commando"}
    await create_hero(base, "ann@example.test", "Pip-Lantern", **commando)

    async def said(client: httpx2.AsyncClient, typed: str) -> str:
        page = await client.get(f"{base}{REDEEM}", params={"key": typed, "from": "form"})
        return visible(page.content)

    async with httpx2.AsyncClient() as bob:
        await post_sign_in(bob, base, "bob@example.test", PASSWORD)
        token = form_token((await bob.get(f"{base}{REDEEM}")).text)
        await bob.post(f"{base}{REDEEM}", data={"csrf": token, "key": "R4TB-9XVE-K7MC-5HGS"})
        no_hero = await said(bob, "W2EH-7MPA-3KUC-9FNY")
    async with httpx2.AsyncClient() as ann:
        await post_sign_in(ann, base, "ann@example.test", PASSWORD)
        unknown = await said(ann, "7KQ4-M2XC-9HTV-RB3O")
        disabled = await said(ann, "K3TF-Q9WA-6NHR-8ELB")
        expired = await said(ann, "J5VN-8QHC-2WKT-6ZDR")
        used_up = await said(ann, "R4TB-9XVE-K7MC-5HGS")
        level = await said(ann, "W2EH-7MPA-3KUC-9FNY")
        token = form_token((await ann.get(f"{base}{REDEEM}")).text)
        await ann.post(f"{base}{REDEEM}", data={"csrf": token, "key": "P8WD-3NZK-H6QT-2YLA"})
        await execute(database_url, "UPDATE wallet SET balance = 2147483000 WHERE currency = '_PF'")
        await execute(database_url, "DELETE FROM product_key_use")
        full = await ann.post(f"{base}{REDEEM}", data={"csrf": token, "key": "P8WD-3NZK-H6QT-2YLA"})

    gone = "This code does not work on this service. Codes use the digits 1-9 and the letters A-Z"
    assert gone in unknown and gone in disabled
    assert "This code ended on 1 Sept 2001, 08:00 UTC." in expired
    assert "This code has been used as often as it allows." in used_up
    assert "The items of this code go to a hero, and the account has none yet." in no_hero
    assert "Create a hero" in no_hero and "This code stays usable for the account." in no_hero
    assert (
        "The items of this code need a hero of National Army, Commando, level 10 or higher."
        " The account's closest, Pip-Lantern, is level 1."
    ) in level
    assert full.status_code == 400
    assert "1,000 Funds would take the account past the largest balance a wallet holds" in full.text
    keep = "SELECT balance FROM wallet WHERE currency = '_PF' AND balance > 2000000000"
    assert await select_value(database_url, keep) == 2147483000


async def test_a_code_and_a_purchase_for_one_hero_and_wallet_at_once_both_go_through(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    await code(database_url, CRATE, {"items": ["1081 7d"], "funds": 100})
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine)
    persona = await SqlPersonaStore(sessions).find_by_id(hero)
    assert persona is not None
    wallet = Wallet("bfheroes", "_PF", persona.account_id, None, 2500)
    week, day = Ownership(RENTED, days=7), Ownership(RENTED, days=1)
    fallback = (2153, Ownership(COUNTED, uses=150))
    grant = CodeGrant(
        CRATE, "bfheroes", persona.account_id, hero, ((1081, week),), fallback, (wallet, 100), None
    )
    bought = Purchase("bfheroes", persona, "1081", "product", wallet, 300, ((1081, day),))
    # Each side waits between its first and second lock until the other took its first, so that
    # the interleaving a deadlock needs happens on every run.
    redeeming, buying = asyncio.Event(), asyncio.Event()

    async def redeemed_item(*args: Any) -> Held | None:
        redeeming.set()
        await buying.wait()
        return await add_item(*args)

    async def bought_item(*args: Any) -> Held | None:
        await redeeming.wait()
        held = await add_item(*args)
        buying.set()
        return held

    monkeypatch.setattr(gift_codes, "add_item", redeemed_item)
    monkeypatch.setattr(purchases, "add_item", bought_item)
    with anyio.fail_after(10):
        await asyncio.gather(
            SqlGiftCodeStore(sessions).redeem(grant), SqlPurchaseStore(sessions).buy(bought)
        )
    await engine.dispose()

    funds = "SELECT balance FROM wallet WHERE currency = '_PF' AND account_id = :account"
    assert await select_value(database_url, funds, account=persona.account_id) == 2500 + 100 - 300
