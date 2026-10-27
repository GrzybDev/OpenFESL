import struct
from typing import Protocol

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Each icon is kept in the database and read whole for every request; a game's icon needs far
# less.
MAX_ICON_SIZE = 1024 * 1024

_ICO_HEADER = struct.Struct("<HHH")
# An ICO directory entry; only where its image lies matters, as each PNG names its own size.
_ICO_ENTRY = struct.Struct("<8xII")
_ICO_ICON = 1


class GameIcons(Protocol):
    """Where the games' icons are kept, as PNG images."""

    async def games(self) -> set[str]:
        """Return the ids of the games that have an icon."""

    async def icon(self, game: str) -> bytes | None:
        """Return a game's icon, if it has one.

        Args:
            game: The game's id.
        """


def icon_png(data: bytes) -> bytes:
    """Return a game's icon as PNG, from a PNG file or the largest PNG image of an ICO file.

    Args:
        data: The file's content.

    Raises:
        ValueError: When the file is neither, its ICO images are all bitmaps, or the image is
            larger than an icon may be.
    """
    if _is_png(data):
        png = data
    elif images := _ico_images(data):
        found = [image for image in images if _is_png(image)]
        if not found:
            raise ValueError("The ICO file holds bitmaps only; save its largest image as PNG")
        png = max(found, key=_pixels)
    else:
        raise ValueError("The file is neither a PNG image nor an ICO file")
    if len(png) > MAX_ICON_SIZE:
        raise ValueError(f"An icon takes at most {MAX_ICON_SIZE // 1024} KiB")
    return png


def _is_png(data: bytes) -> bool:
    # The signature, then the header chunk that holds the image's size.
    return len(data) >= 24 and data.startswith(PNG_SIGNATURE) and data[12:16] == b"IHDR"


def _pixels(png: bytes) -> int:
    width, height = struct.unpack_from(">II", png, 16)
    return int(width * height)


def _ico_images(data: bytes) -> list[bytes]:
    if len(data) < _ICO_HEADER.size:
        return []
    reserved, kind, count = _ICO_HEADER.unpack_from(data)
    if reserved != 0 or kind != _ICO_ICON:
        return []
    images = []
    for index in range(count):
        at = _ICO_HEADER.size + index * _ICO_ENTRY.size
        if at + _ICO_ENTRY.size > len(data):
            break
        size, offset = _ICO_ENTRY.unpack_from(data, at)
        # An image that a damaged file cuts short is left out.
        if offset + size <= len(data):
            images.append(data[offset : offset + size])
    return images
