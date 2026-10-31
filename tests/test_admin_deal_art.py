from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import httpx2
import pytest
from heroes import MAGMA
from identity import IdentityProvider, form_token, sign_in
from PIL import Image
from store import attribute, deals, stock, without_rotation
from support import fetch, form_fields, visible
from test_admin_heroes import KEEPER

from openfesl.core.store import Bundle
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio


def png(width: int, height: int) -> bytes:
    """Return a picture of one colour as a PNG file."""
    out = BytesIO()
    Image.new("RGBA", (width, height), (196, 64, 32, 255)).save(out, "PNG")
    return out.getvalue()


@pytest.mark.usefixtures("prompt_actions")
async def test_an_uploaded_picture_takes_the_generated_ones_place_until_it_is_reset(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    number = await products.add_bundle("bfheroes", Bundle("Invented Set", None, "_PF", 2000))
    game_big = f"/static/hotdeals/{number}_big.png"
    generated = (await fetch(server_url, game_big, MAGMA))[2]
    art = f"{base}/admin/store/bfheroes/deals/art/{number}"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        page = await sign_in(client, base, next="/admin/store/bfheroes/deals/home")
        assert "The game's home screen Rule: the first two deals big" in visible(page.content)
        assert "Large 1 By rule Invented Set Generated" in visible(page.content)
        token = form_token(page.text)
        wrong = {"big": ("wide.png", png(400, 300), "image/png")}
        refused = await client.post(art, data={"csrf": token}, files=wrong)
        assert refused.status_code == 400
        assert (
            "The big picture is a PNG 308 px wide and up to 355 px tall; wide.png is not."
            in visible(refused.content)
        )
        picture = png(308, 300)
        right = {"big": ("set.png", picture, "image/png")}
        saved = await client.post(art, data={"csrf": token}, files=right, follow_redirects=True)
        assert "Saved the uploaded pictures of Invented Set." in visible(saved.content)
        assert "Large 1 By rule Invented Set Uploaded" in visible(saved.content)
        # The game downloads the upload, and the panel shows what the game gets.
        assert (await fetch(server_url, game_big, MAGMA))[2] == picture
        assert (await client.get(f"{art}_big.png")).content == picture

        reset = await client.post(f"{art}/reset", data={"csrf": token}, follow_redirects=True)
        assert "Invented Set shows its generated pictures again." in visible(reset.content)
        assert (await fetch(server_url, game_big, MAGMA))[2] == generated
        log = visible((await client.get(f"{base}/admin/audit")).content)
        assert "Deal pictures reset Invented Set" in log
        assert "Deal picture uploaded Invented Set Details big picture" in log


@pytest.mark.usefixtures("prompt_actions")
async def test_a_pinned_offer_comes_first_in_its_section_and_right_after_the_big_deals(
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    starts, ends = now - timedelta(days=1), now + timedelta(days=6)
    numbers = [
        await products.add_bundle(
            "bfheroes",
            Bundle(
                name, None, "_PF", 2000, starts=starts, ends=ends, members=(1,), section=section
            ),
        )
        for name, section in (
            ("Invented Daily", "daily"),
            ("Invented New", "new"),
            ("Invented Popular", "popular"),
            ("Invented Pinned", "popular"),
        )
    ]
    home = f"{base}/admin/store/bfheroes/deals/home"
    offer = f"{base}/admin/store/bfheroes/deals/offers/{numbers[3]}"
    admin_provider.user = KEEPER
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next=home[len(base) :])).text)
        before = await deals(server_url, "en")
        pinned = await client.post(
            f"{home}/{numbers[3]}/pin", data={"csrf": token, "pinned": "1"}, follow_redirects=True
        )
        after = await deals(server_url, "en")
        page = visible((await client.get(f"{base}/bfheroes/store/hot-deals")).content)
        editor = (await client.get(offer)).text
        # Saving the offer's editor without its Pinned box unpins it.
        fields = form_fields(editor)
        del fields["pinned"]
        await client.post(offer, data=fields)
        unpinned = await deals(server_url, "en")
        log = visible((await client.get(f"{base}/admin/audit")).content)

    def names(listed: list[ElementTree.Element]) -> list[str | None]:
        return [attribute(deal, "Product Name") for deal in listed]

    assert names(before) == [
        "Invented Daily",
        "Invented New",
        "Invented Popular",
        "Invented Pinned",
    ]
    assert "Pinned Invented Pinned: it comes first in its section" in visible(pinned.content)
    assert "Small 1 Pinned Invented Pinned" in visible(pinned.content)
    assert names(after) == ["Invented Daily", "Invented New", "Invented Pinned", "Invented Popular"]
    assert page.index("Invented Pinned") < page.index("Invented Popular")
    assert form_fields(editor)["pinned"] == ["1"]
    assert names(unpinned) == names(before)
    assert "Offer pinned Invented Pinned" in log and "Offer unpinned Invented Pinned" in log
