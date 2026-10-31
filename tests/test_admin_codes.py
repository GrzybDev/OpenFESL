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


async def test_admins_make_change_and_switch_codes_that_moderators_read_in_full(
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
            "kind": "single",
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

        # A change applies to later redemptions; disabling asks for a note.
        edited = form | {"limit": "any", "ends_day": "", "note": "More players than planned"}
        await client.post(f"{base}{page}/edit", data=edited)
        changed = visible((await client.get(base + page)).content)
        state = f"{base}{page}/state"
        unnoted = await client.post(state, data={"csrf": token, "enabled": "0", "note": ""})
        await client.post(state, data={"csrf": token, "enabled": "0", "note": "Test finished"})
        disabled = visible(
            (
                await client.get(f"{base}{KEYS}", params={"game": "bfheroes", "state": "disabled"})
            ).content
        )

        batch = {
            "csrf": token,
            "game": "bfheroes",
            "kind": "batch",
            "count": "3",
            "keys": "3",
            "label-en": "Invented newsletter keys",
            "note": "Invented newsletter",
        }
        counted = await client.post(f"{base}{NEW}", data=batch | {"count": "1001"})
        batch_page = (await client.post(f"{base}{NEW}", data=batch)).headers["location"]
        batch_shown = visible((await client.get(base + batch_page)).content)
        download = await client.get(f"{base}{batch_page}/codes.txt")
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
    assert "Changes to" in changed and "0 so far, no limit" in changed and "No end" in changed
    assert unnoted.status_code == 400 and "Disable this code?" in unnoted.text
    assert code in disabled and "Disabled 1" in disabled
    assert counted.status_code == 400 and "A batch makes 1 to 1,000 codes." in counted.text
    assert "3 codes made." in batch_shown and "Batch · 3 codes" in batch_shown
    assert "One account per code · 0 of 3 used" in batch_shown
    codes = download.text.splitlines()
    assert len(set(codes)) == 3 and all(FORMAT.fullmatch(found) for found in codes)
    assert 'filename="invented-newsletter-keys.txt"' in download.headers["content-disposition"]
    assert "Code made" in audit and "Batch made" in audit and "Code changed" in audit
    assert "Accounts: At most 300 accounts → Any number of accounts" in audit
    assert "Code disabled" in audit and "Test finished" in audit
    # The codes are the site's alone: Bad Company 2's keys leave them out, and the game's own
    # key prompt refuses them as unknown.
    assert code not in tags and "ACTIVATE-VETERAN" in tags
    async with game(server_url) as ann:
        await log_in_player(ann, FESL, "ann@example.test")
        redeem = [("TXN", "NuEntitleUser"), ("key", codes[0])]
        assert (await ann.request(FESL, "acct", redeem))["errorCode"] == "181"

    admin_provider.user = WARDEN
    async with httpx2.AsyncClient() as client:
        read = await sign_in(client, base, next=f"{KEYS}?game=bfheroes")
        seen = visible((await client.get(base + page)).content)
        moderator_download = await client.get(f"{base}{batch_page}/codes.txt")
        new = await client.get(f"{base}{NEW}", params={"game": "bfheroes"})
        switch = await client.post(state, data={"csrf": form_token(read.text), "note": "No"})

    assert code in visible(read.content) and "New code" not in visible(read.content)
    assert "Codes are made and changed with the admin role." in seen and code in seen
    assert moderator_download.text.splitlines() == codes
    assert new.status_code == 403 and switch.status_code == 403


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
        fields = {"csrf": token, "game": "bfheroes", "kind": "single", "funds": "0", "keys": "0"}
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
