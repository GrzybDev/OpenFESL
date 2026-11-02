from io import BytesIO

import pytest
from PIL import Image

from openfesl.formats.icons import png, webp


def picture(form: str, **options: str) -> bytes:
    """Return an invented 4x2 picture with see-through pixels, saved in a format."""
    source = Image.new("RGBA", (4, 2), (200, 40, 10, 255))
    source.putpixel((0, 0), (0, 0, 0, 0))
    source.putpixel((3, 1), (30, 90, 250, 128))
    saved = BytesIO()
    source.save(saved, form, **options)
    return saved.getvalue()


def pixels(data: bytes) -> bytes:
    with Image.open(BytesIO(data)) as decoded:
        return decoded.convert("RGBA").tobytes()


@pytest.mark.parametrize(
    ("form", "options"),
    [("DDS", {}), ("DDS", {"pixel_format": "DXT5"}), ("TGA", {}), ("PNG", {})],
)
def test_an_icon_becomes_a_webp_picture_with_the_same_pixels(
    form: str, options: dict[str, str]
) -> None:
    source = picture(form, **options)

    converted = webp(source)

    assert converted[:4] == b"RIFF" and converted[8:12] == b"WEBP"
    assert pixels(converted) == pixels(source)


def test_a_file_that_is_no_picture_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be read"):
        webp(b"<html></html>")
    with pytest.raises(ValueError, match="cannot be read"):
        webp(picture("GIF"))


def test_a_lossy_picture_keeps_its_size_and_transparency() -> None:
    converted = webp(picture("TGA"), quality=90)

    assert b"VP8 " in converted and b"ALPH" in converted
    with Image.open(BytesIO(converted)) as decoded:
        assert decoded.size == (4, 2)
        assert decoded.getchannel("A").getpixel((0, 0)) == 0


def test_a_kept_icon_becomes_a_png_picture_with_the_same_pixels() -> None:
    kept = webp(picture("PNG"))

    converted = png(kept)

    assert converted.startswith(b"\x89PNG\r\n\x1a\n")
    assert pixels(converted) == pixels(kept)
