import html
import os
import re
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import anyio
import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from playwright.async_api import Browser, Page
from support import cli
from test_catalogue_import import extracted

from openfesl.core.catalogue import CatalogueFile

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-9", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-9", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}
PAGE = "/admin/catalogues"


def unpacking_to(size: int) -> bytes:
    """Return a ZIP archive whose one icon unpacks to a number of bytes."""
    written = BytesIO()
    info = ZipInfo("icons/item1.webp")
    info.compress_type = ZIP_DEFLATED
    with ZipFile(written, "w") as archive, archive.open(info, "w", force_zip64=True) as icon:
        chunk = bytes(2**20)
        for _ in range(size // len(chunk)):
            icon.write(chunk)
    return written.getvalue()


def facts(page: str, label: str) -> list[str]:
    """Return the values of a page's facts with a label, as their text reads."""
    found = re.findall(rf"<dt>{label}</dt>\s*<dd[^>]*>(.*?)</dd>", page, re.DOTALL)
    return [" ".join(re.sub(r"<[^>]+>", "", value).split()) for value in found]


def of_game(file: bytes, game: str) -> bytes:
    """Return a catalogue's file as if another game's copy had made it."""
    catalogue = CatalogueFile.read(file)
    return CatalogueFile(catalogue.document.model_copy(update={"game": game}), {}, {}).write()


async def send(
    client: httpx2.AsyncClient, base: str, token: str, name: str, data: bytes | None
) -> str:
    """Send the import's form as a browser without scripts does, returning the page after it."""
    files = {"file": (name, data, "application/zip")} if data is not None else None
    sent = await client.post(
        base + PAGE, data={"csrf": token, "confirmed": "1"}, files=files or {"file": ("", b"")}
    )
    assert sent.status_code == 303, sent.text
    return html.unescape((await client.get(base + PAGE)).text)


@pytest.mark.usefixtures("prompt_actions")
async def test_admins_and_moderators_see_what_each_games_catalogue_holds(
    files: Path,
    admin_provider: IdentityProvider,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        before = html.unescape((await sign_in(client, base, next=PAGE)).text)
        # The menu offers the page, as a served game takes a catalogue; a game without catalogue
        # rules is listed as taking none.
        assert f'href="{PAGE}"' in before and 'aria-current="page"' in before
        assert "No catalogue imported yet." in before and "Not imported" in before
        assert "Battlefield: Bad Company 2" in before and "Takes no catalogue" in before

        await cli(capsys, "catalogue", "import", str(await extracted(tmp_path, capsys)))
        with anyio.fail_after(5):
            while "No catalogue imported" in (imported := (await client.get(base + PAGE)).text):
                await anyio.sleep(0.05)
        imported = html.unescape(imported)
        log = (await client.get(f"{base}/admin/audit")).text

    # The import came from the command line, which the admin's log names as no admin.
    assert facts(imported, "Imported")[0].endswith(" UTC · The command line")
    assert facts(imported, "Items") == ["12"]
    assert facts(imported, "Languages") == ["2"] and "English and Polish" in imported
    assert facts(imported, "Icons") == ["5 kept"]
    assert facts(imported, "Hero models") == ["4 kept · 2 textures"]
    assert facts(imported, "Daily missions") == ["366 days"]
    assert facts(imported, "XP per level") == ["30 levels"]
    # Items by kind, most first.
    kinds = [(kind, facts(imported, kind)) for kind in ("Missions", "Abilities", "Appearance")]
    assert kinds == [("Missions", ["4"]), ("Abilities", ["3"]), ("Appearance", ["2"])]
    order = [imported.index(f"<dt>{kind}</dt>") for kind in ("Missions", "Abilities", "Weapons")]
    assert order == sorted(order)
    # The audit log's row of the import opens the game's block.
    assert f'href="{PAGE}#game-bfheroes"' in log

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = html.unescape((await sign_in(client, base, next=PAGE)).text)
    assert facts(seen, "Items") == ["12"]


async def test_admins_import_a_catalogue_on_the_page_where_each_refusal_says_why(
    files: Path,
    admin_provider: IdentityProvider,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    first = (await extracted(tmp_path, capsys)).read_bytes()
    old = CatalogueFile.read(first)
    later = CatalogueFile(
        old.document.model_copy(update={"items": old.document.items[:2]}),
        {"en": {"WEST_Custom_Item_2": "Invented Hair"}},
        {"item1": old.icons["item1"]},
    ).write()
    format_two = BytesIO()
    with ZipFile(format_two, "w") as archive:
        archive.writestr("catalogue.json", '{"format": 2, "game": "bfheroes", "items": []}')

    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        page = html.unescape((await sign_in(client, base, next=PAGE)).text)
        token = form_token(page)
        assert 'openfesl catalogue extract "path/to/Battlefield Heroes"' in page
        assert "openfesl catalogue import bfheroes-catalogue.zip" in page

        # Each refusal names its own reason, and none changes the catalogue.
        refused = {
            "invented.zip is not a catalogue file.": ("invented.zip", b"invented"),
            "This catalogue file has format 2; this service reads format 1.": (
                "old.zip",
                format_two.getvalue(),
            ),
            "This file holds the catalogue of Battlefield: Bad Company 2, which this service "
            "serves without a catalogue.": ("bfbc2.zip", of_game(first, "bfbc2")),
            "This file holds the catalogue of a game this service does not serve (mohairborne).": (
                "airborne.zip",
                of_game(first, "mohairborne"),
            ),
            "This file unpacks to more than 256\u00a0MB, more than any catalogue holds.": (
                "bomb.zip",
                unpacking_to(257 * 2**20),
            ),
        }
        for reason, (name, data) in refused.items():
            answer = await send(client, base, token, name, data)
            assert reason in answer and "The catalogue stays as it was." in answer, reason
            assert "No catalogue imported yet." in answer
        assert "Choose a catalogue file first." in await send(client, base, token, "", None)

        # A first import lists its new numbers alone, as does the block, with who imported it.
        imported = await send(client, base, token, "bfheroes-catalogue.zip", first)
        assert "Battlefield Heroes uses the new catalogue." in imported
        assert "The audit log records the import." in imported
        assert facts(imported, "Items") == ["12", "12"]
        assert facts(imported, "Icons") == ["5 kept", "5"]
        assert facts(imported, "Imported")[0].endswith(" UTC · invented-keeper")
        assert "data-count-from" not in imported
        # From now on the form asks before it replaces the catalogue.
        assert "data-replaces" in imported
        assert "Replace the current catalogue of Battlefield Heroes as a whole" in imported

        # A replacement compares the numbers, and the block counts from the old ones.
        replaced = await send(client, base, token, "bfheroes-catalogue.zip", later)
        for name, before, after in (("Items", 12, 2), ("Languages", 2, 1), ("Icons", 5, 1)):
            kept = f"{after} kept" if name == "Icons" else str(after)
            assert facts(replaced, name) == [kept, f"{before} → {after}"], name
        assert re.search(r'data-count="2"\s+data-count-from="12"', replaced)
        assert len(list((files / "catalogues" / "bfheroes").iterdir())) == 1

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = html.unescape((await sign_in(client, base, next=PAGE)).text)
        assert "Catalogues are imported with the admin role." in seen
        assert "data-catalogue-upload" not in seen
        attempt = {"file": ("bfheroes-catalogue.zip", first, "application/zip")}
        denied = await client.post(base + PAGE, data={"csrf": form_token(seen)}, files=attempt)
        assert denied.status_code == 403


async def importing(browser: Browser, base: str, client: httpx2.AsyncClient) -> Page:
    """Open the catalogues page in a new window of a browser, with the session of a client."""
    context = await browser.new_context(locale="en-GB")
    for name, value in client.cookies.items():
        await context.add_cookies([{"name": name, "value": value, "url": base}])
    page = await context.new_page()
    await page.goto(base + PAGE)
    return page


@pytest.mark.usefixtures("scripts")
async def test_the_page_sends_the_file_in_the_background_and_asks_before_a_replacement(
    files: Path,
    admin_provider: IdentityProvider,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edge: Browser,
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    file = await extracted(tmp_path, capsys)
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        await sign_in(client, base, next=PAGE)
        page = await importing(edge, base, client)
    chooser = page.locator("input[type=file]")
    send = page.get_by_role("button", name="Import catalogue")
    result = page.locator("[data-catalogue-result]")

    # Without a file the field says what is missing.
    await send.click()
    await page.get_by_text("Choose a catalogue file first.").wait_for()
    # A first import replaces nothing, so it asks nothing first.
    await chooser.set_input_files(file)
    name = page.locator("[data-catalogue-file-name]")
    assert re.fullmatch(r"catalogue\.zip · [0-9.]+\u00a0MB", await name.inner_text())
    await send.focus()
    await page.keyboard.press("Enter")
    await result.get_by_text("Battlefield Heroes uses the new catalogue.").wait_for()
    # The page's blocks came in place of the old ones, with the form cleared, and the keyboard
    # goes on from the result.
    assert await page.get_by_text("No catalogue imported yet.").count() == 0
    assert await name.inner_text() == "No file chosen"
    await page.keyboard.press("Tab")
    audit = result.get_by_role("link", name="The audit log records the import.")
    assert await audit.evaluate("link => link === document.activeElement")

    # A replacement asks first; cancelling keeps the chosen file.
    await chooser.set_input_files(file)
    await send.click()
    dialog = page.get_by_role("dialog", name="Replace the catalogue of Battlefield Heroes?")
    await dialog.wait_for()
    assert "invented-keeper" in await dialog.inner_text()
    await dialog.get_by_role("button", name="Cancel").click()
    await dialog.wait_for(state="detached")
    assert (await name.inner_text()).startswith("catalogue.zip")

    # Stopping the upload leaves the catalogue as it was and the file chosen; a large file over a
    # slow link stays on its way long enough.
    large = tmp_path / "large" / "catalogue.zip"
    large.parent.mkdir()
    large.write_bytes(os.urandom(8 * 2**20))
    await chooser.set_input_files(large)
    network = await page.context.new_cdp_session(page)
    await network.send("Network.enable")
    await network.send(
        "Network.emulateNetworkConditions",
        {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": 65536},
    )
    await send.click()
    await dialog.get_by_role("button", name="Replace").click()
    await page.get_by_text("Leaving this page stops the upload").wait_for()
    # The keyboard is on the stop while the file goes up, and back on the import once stopped.
    await page.keyboard.press("Enter")
    await result.get_by_text("catalogue.zip was not sent in full.").wait_for()
    assert "The catalogue stays as it was; the file stays chosen." in await result.inner_text()
    assert await send.evaluate("button => button === document.activeElement")
    await network.send(
        "Network.emulateNetworkConditions",
        {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
    )
    await chooser.set_input_files(file)
    await send.click()
    await dialog.get_by_role("button", name="Replace").click()
    await result.get_by_text("Battlefield Heroes uses the new catalogue.").wait_for()

    # A file larger than any catalogue is refused before anything is sent.
    await page.locator("[data-catalogue-upload]").evaluate("form => form.dataset.most = '64'")
    sent: list[str] = []
    page.on("request", lambda request: sent.append(request.method))
    await chooser.set_input_files(file)
    await send.click()
    refusal = "a catalogue file holds at most 256 MB, so it was not sent."
    await result.get_by_text(refusal).wait_for()
    assert "POST" not in sent
