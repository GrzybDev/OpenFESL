import re
from pathlib import Path

import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from living import NATIONAL, ROYAL, import_catalogue, worn
from support import FESL, game, log_in_player, visible
from test_admin_keys import KEEPER, WARDEN

pytestmark = pytest.mark.anyio
KEYS = "/admin/keys"
NEW = "/admin/keys/codes/new"
# A Royal and a National hat, the shirt that the game data withholds and a tonic sold as uses.
ITEMS = {
    1081: worn("Invented Mask", ROYAL, "0"),
    1084: worn("Invented Visor", NATIONAL, "0"),
    257: worn("Invented Logo Shirt", ROYAL, "3"),
    2153: ("Invented Tonic", "ability", {"consumable": "true"}),
}
FORMAT = re.compile(r"[1-9A-HJ-NP-Z]{4}(-[1-9A-HJ-NP-Z]{4}){3}")


def drawn(page: str) -> str:
    """Return the code that a new code's editor drew."""
    found = re.search(r'name="code"[^>]*value="([^"]+)"', page)
    assert found and FORMAT.fullmatch(found.group(1))
    return found.group(1)


async def test_admins_make_codes_that_moderators_read_in_full(
    admin_provider: IdentityProvider, base: str, server_url: str
) -> None:
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        empty = await sign_in(client, base, next=f"{KEYS}?game=bfheroes")
        token = form_token(empty.text)
        editor = (await client.get(f"{base}{NEW}", params={"game": "bfheroes"})).text
        code = drawn(editor)
        form = {
            "csrf": token,
            "game": "bfheroes",
            "code": code,
            "funds": "500",
            "keys": "2",
            "limit": "limit",
            "most": "300",
            "ends_day": "2099-11-02",
            "ends_time": "08:00",
            "label-en": "Invented crate",
            "label-pl": "Wymyślona skrzynia",
            "note": "Gift of an invented announcement",
        }
        # Nothing is made while a field is refused, and each says why beside it.
        refused = await client.post(f"{base}{NEW}", data=form | {"label-en": "", "note": ""})
        past = await client.post(f"{base}{NEW}", data=form | {"ends_day": "2001-01-01"})
        nothing = await client.post(f"{base}{NEW}", data=form | {"funds": "0", "keys": "0"})
        made = await client.post(f"{base}{NEW}", data=form)
        page = made.headers["location"]
        shown = visible((await client.get(base + page)).content)
        listed = visible((await client.get(f"{base}{KEYS}", params={"game": "bfheroes"})).content)
        audit = visible((await client.get(f"{base}/admin/audit")).content)
        tags = visible((await client.get(f"{base}{KEYS}", params={"game": "bfbc2"})).content)

    assert "No codes for Battlefield Heroes yet." in visible(empty.content)
    assert refused.status_code == 400
    assert "The English label is required; players see it on the redeem page." in refused.text
    assert "A note is required; it goes into the audit log." in refused.text
    assert (
        past.status_code == 400 and "The end, 1 Jan 2001, 08:00 UTC, is in the past." in past.text
    )
    assert "A code needs at least one item, Funds or keys." in nothing.text
    assert page == f"/admin/keys/codes/{code}"
    assert f"Code {code} made." in shown
    assert "Contents 500 Funds · 2 treasure chest keys" in shown
    assert "Each account once · 0 of 300 used" in shown and "2 Nov 2099, 08:00 UTC" in shown
    assert "Invented crate · Wymyślona skrzynia" in shown
    assert "by invented-keeper: Gift of an invented announcement" in shown
    contents = "Contents 500 Funds · 2 treasure chest keys for the account"
    assert f"{code} Invented crate {contents} Uses 0 of 300" in listed
    assert "Code made" in audit and "Gift of an invented announcement" in audit
    # The codes are the site's alone: Bad Company 2's keys leave them out, and the game's own
    # key prompt refuses them as unknown.
    assert code not in tags and "ACTIVATE-VETERAN" in tags
    async with game(server_url) as ann:
        await log_in_player(ann, FESL, "ann@example.test")
        redeem = [("TXN", "NuEntitleUser"), ("key", code)]
        assert (await ann.request(FESL, "acct", redeem))["errorCode"] == "181"

    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        read = await sign_in(client, base, next=f"{KEYS}?game=bfheroes")
        seen = visible((await client.get(base + page)).content)
        new = await client.get(f"{base}{NEW}", params={"game": "bfheroes"})

    assert code in visible(read.content) and "New code" not in visible(read.content)
    assert "Codes are made with the admin role." in seen and code in seen
    assert new.status_code == 403


async def test_a_codes_items_come_from_the_catalogue_and_one_hero_takes_them_all(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys, ITEMS)
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=f"{NEW}?game=bfheroes")).text)
        fields = {"csrf": token, "game": "bfheroes", "funds": "0", "keys": "0"}
        found = await client.post(f"{base}{NEW}", data=fields | {"q": "mask", "find": "1"})
        mask = {"q": "mask", "pick": "1081", "own": "days", "days": "7", "add": "1"}
        added = await client.post(f"{base}{NEW}", data=fields | mask)
        visor = {"q": "visor", "pick": "1084", "own": "perm", "add": "1", "item": "1081 7d"}
        clash = await client.post(f"{base}{NEW}", data=fields | visor)
        tonic = {"q": "tonic", "pick": "2153", "own": "days", "days": "7", "add": "1"}
        counted = await client.post(f"{base}{NEW}", data=fields | tonic)
        shirt = await client.post(f"{base}{NEW}", data=fields | {"q": "257", "pick": "257"})
        # Without scripts a choice is answered by the search's button; with them, live.
        live = await client.get(
            f"{base}{NEW}", params={"game": "bfheroes", "q": "ton", "pick": "2153"}
        )

    assert "Invented Mask" in visible(found.content) and "Royal Army" in visible(found.content)
    assert re.search(r'name="item"\s+value="1081 7d"', added.text)
    assert "Invented Mask · 7 days" in visible(added.content)
    both = "Invented Visor for National Army: no hero could take both."
    assert f"Invented Mask is for Royal Army and {both}" in visible(clash.content)
    assert not re.search(r'name="item"\s+value="1084 perm"', clash.text)
    uses = "Invented Tonic is counted in uses; it cannot be given for days or for good."
    assert uses in visible(counted.content)
    withheld = "Item 257 is withheld on this service: codes and rewards never give it."
    assert withheld in visible(shirt.content)
    assert "Add Invented Tonic, 10 uses" in visible(live.content)
