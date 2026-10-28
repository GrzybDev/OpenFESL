from io import BytesIO

from PIL import Image


def size(png: bytes, longest: int) -> tuple[int, int]:
    """Return the width and height of a PNG picture that decodes whole.

    The picture's size is read from its header before its pixels are unpacked, so a small file
    of a huge picture is refused unread.

    Args:
        png: The picture.
        longest: The most pixels that either side may have.

    Raises:
        ValueError: When the data is no PNG picture, does not decode, or a side is longer.
    """
    try:
        with Image.open(BytesIO(png), formats=("PNG",)) as picture:
            width, height = picture.size
            if max(width, height) > longest:
                raise ValueError(f"the picture is {width} x {height} pixels")
            picture.load()
    # Pillow reports some broken headers as SyntaxError.
    except (OSError, SyntaxError) as error:
        raise ValueError(f"the picture cannot be read: {error}") from None
    return width, height
