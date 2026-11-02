from io import BytesIO

from PIL import Image

# The formats of the pictures that games' menus draw icons from.
_SOURCES = ("DDS", "TGA", "PNG")


def webp(data: bytes, quality: int | None = None) -> bytes:
    """Return a picture as a WebP picture with its transparency, as browsers show it.

    Args:
        data: The picture as a DDS, TGA or PNG file.
        quality: The quality of a lossy picture, from 0 to 100; lossless without one.

    Raises:
        ValueError: When the picture is in none of those formats or cannot be decoded.
    """
    try:
        with Image.open(BytesIO(data), formats=_SOURCES) as picture:
            pixels = picture.convert("RGBA")
    # Pillow reports some broken headers as SyntaxError.
    except (OSError, SyntaxError) as error:
        raise ValueError(f"the picture cannot be read: {error}") from None
    converted = BytesIO()
    if quality is None:
        pixels.save(converted, "WEBP", lossless=True)
    else:
        # Exact keeps the colour of transparent pixels, which filtering blends into the edges.
        pixels.save(converted, "WEBP", quality=quality, exact=True)
    return converted.getvalue()


def png(data: bytes) -> bytes:
    """Return a WebP picture as a PNG picture with its transparency, for browsers without WebP.

    Args:
        data: The picture, as an import keeps its icons.
    """
    with Image.open(BytesIO(data), formats=("WEBP",)) as picture:
        pixels = picture.convert("RGBA")
    converted = BytesIO()
    pixels.save(converted, "PNG", optimize=True)
    return converted.getvalue()
