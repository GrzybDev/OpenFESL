from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from heroes import MAGMA
from PIL import Image
from store import ITEMS, ROYAL_SOLDIER, attribute, deals, shopper, stock, without_rotation
from support import Products, fetch

from openfesl.core.store import Bundle
from openfesl.formats.icons import webp

pytestmark = pytest.mark.anyio

# The invented catalogue's products: the Royal coat for good, the Royal pet and 25 bandages.
COAT, PET, BANDAGES = 1, 5, 6
# The colour of the coat's and the pet's invented icons.
GREEN = (20, 200, 60, 255)


@pytest.mark.usefixtures("files", "prompt_actions", "base")
async def test_the_game_downloads_each_hot_deals_pictures_from_the_addresses_it_is_given(
    server_url: str,
    database_url: str,
    products: Products,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    icon = BytesIO()
    Image.new("RGBA", (64, 64), GREEN).save(icon, "PNG")
    with_icons = [
        entry.model_copy(update={"icon": f"item{entry.id}"}) if entry.id in (3, 521) else entry
        for entry in ITEMS
    ]
    await stock(
        server_url,
        tmp_path,
        capsys,
        with_icons,
        dict.fromkeys(("item3", "item521"), webp(icon.getvalue())),
    )
    await without_rotation(database_url)
    set_number = await products.add_bundle(
        "bfheroes",
        Bundle(
            "Invented Set", None, "_PF", 2000, original_price=3675, members=(COAT, PET, BANDAGES)
        ),
    )
    [listed] = await deals(server_url, "en")

    sizes = {}
    for name in ("gameBigImage", "gameSmallImage"):
        address = urlsplit(attribute(listed, name) or "")
        # The game turns the address into a file name, which a query or a port would break.
        assert (address.scheme, address.netloc, address.query) == ("http", MAGMA, "")
        assert address.path.startswith(f"/static/hotdeals/{set_number}_")
        status, headers, body = await fetch(server_url, address.path, MAGMA)
        assert status == 200 and headers["Content-Type"] == "image/png"
        with Image.open(BytesIO(body)) as picture:
            sizes[name] = (picture.size, picture.mode)
    assert sizes == {"gameBigImage": ((308, 355), "RGBA"), "gameSmallImage": ((200, 195), "RGBA")}
    for missing in (f"/static/hotdeals/{set_number + 1000}_big.png", "/static/hotdeals/x.png"):
        assert (await fetch(server_url, missing, MAGMA))[0] == 404

    # The game counts as signed in through its session, to whom the game's art is shown by
    # default, so its own downloads show the items' imported icons; without it, plain tiles.
    cookie, _ = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    big = urlsplit(attribute(listed, "gameBigImage") or "").path
    shown = []
    for game in (cookie, {}):
        with Image.open(BytesIO((await fetch(server_url, big, MAGMA, game))[2])) as picture:
            found = picture.convert("RGBA").getcolors(picture.width * picture.height) or []
        shown.append(GREEN in [colour for _, colour in found])
    assert shown == [True, False]
