from io import BytesIO

from PIL import Image

# The formats of the pictures that games' menus draw icons from.
_SOURCES = ("DDS", "TGA", "PNG")


def webp(data: bytes) -> bytes:
    """Return a picture as a lossless WebP picture with its transparency, as browsers show it.

    Args:
        data: The picture as a DDS, TGA or PNG file.

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
    pixels.save(converted, "WEBP", lossless=True)
    return converted.getvalue()
