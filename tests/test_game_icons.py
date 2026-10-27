import struct
from pathlib import Path

import pytest
from support import cli, fetch, png_image

from openfesl.core.game_icons import icon_png
from openfesl.web.game_icons import reaches_discord

pytestmark = pytest.mark.anyio

# The start of an invented bitmap image, as older icon files hold their images.
BITMAP = struct.pack("<IiiHH", 40, 16, 32, 1, 32) + bytes(64)


def ico(*images: bytes) -> bytes:
    """Return an ICO file that holds images in the order given."""
    directory = struct.pack("<HHH", 0, 1, len(images))
    offset = len(directory) + 16 * len(images)
    for image in images:
        directory += struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return directory + b"".join(images)


async def test_an_icon_stored_from_an_ico_file_is_served_as_its_largest_png(
    server_url: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    largest = png_image(64, 64)
    file = tmp_path / "invented.ico"
    file.write_bytes(ico(png_image(16, 16), BITMAP, largest, png_image(32, 32)))
    await cli(capsys, "icons", "set", "bfbc2", str(file))

    status, headers, body = await fetch(server_url, "/bfbc2/icon.png")
    assert (status, headers["Content-Type"], body) == (200, "image/png", largest)
    assert headers["Cache-Control"] == "public, max-age=3600"
    # Discord's image proxy and browsers ask again with the tag of the copy they keep.
    again = await fetch(server_url, "/bfbc2/icon.png", headers={"If-None-Match": headers["ETag"]})
    assert again[0] == 304

    await cli(capsys, "icons", "remove", "bfbc2")
    assert (await fetch(server_url, "/bfbc2/icon.png"))[0] == 404


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(ico(BITMAP), id="bitmaps-only"),
        pytest.param(ico(png_image(16, 16))[:40], id="cut-short"),
        pytest.param(b"GIF89a" + bytes(32), id="other-format"),
    ],
)
def test_a_file_without_a_whole_png_image_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError):
        icon_png(data)


@pytest.mark.parametrize(
    ("public_url", "reaches"),
    [
        ("https://fesl.example.com", True),
        ("https://203.0.113.7", False),
        ("http://fesl.example.com", False),
        ("https://localhost:8000", False),
        ("https://192.168.1.10", False),
        (None, False),
    ],
)
def test_discord_reaches_presence_images_only_at_a_public_https_address(
    public_url: str | None, reaches: bool
) -> None:
    assert reaches_discord(public_url) is reaches
