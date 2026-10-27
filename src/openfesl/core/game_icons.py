import math
import struct
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Each icon is kept in the database and read whole for every request; a game's icon needs far
# less.
MAX_ICON_SIZE = 1024 * 1024

_ICO_HEADER = struct.Struct("<HHH")
# An ICO directory entry; only where its image lies matters, as each PNG names its own size.
_ICO_ENTRY = struct.Struct("<8xII")
_ICO_ICON = 1


class IconProblem(StrEnum):
    """Why a file gives no icon."""

    FORMAT = "format"
    """It is neither a PNG image nor an ICO file."""
    BITMAPS = "bitmaps"
    """It is an ICO file whose images are all bitmaps."""
    SIZE = "size"
    """Its image is larger than an icon may be."""


# What the command line says of each problem; the web pages say it in the reader's language.
_PROBLEMS = {
    IconProblem.FORMAT: "The file is neither a PNG image nor an ICO file",
    IconProblem.BITMAPS: "The ICO file holds bitmaps only; save its largest image as PNG",
    IconProblem.SIZE: f"An icon takes at most {MAX_ICON_SIZE // 1024} KiB",
}


class IconRefused(ValueError):
    """Raised for a file that gives no icon, saying why."""

    def __init__(self, problem: IconProblem) -> None:
        """Say why the file gives no icon.

        Args:
            problem: The reason.
        """
        super().__init__(_PROBLEMS[problem])
        self.problem = problem


@dataclass(frozen=True, slots=True)
class StoredIcon:
    """A game's icon with when it was stored and by whom.

    Attributes:
        png: The icon as a PNG image.
        at: When it was stored, if the log of the admin's actions still says.
        by: The display name of the admin who stored it; None for the command line.
    """

    png: bytes
    at: datetime | None
    by: str | None


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
        IconRefused: When the file is neither, its ICO images are all bitmaps, or the image is
            larger than an icon may be.
    """
    if _is_png(data):
        png = data
    elif images := _ico_images(data):
        found = [image for image in images if _is_png(image)]
        if not found:
            raise IconRefused(IconProblem.BITMAPS)
        png = max(found, key=lambda image: math.prod(png_size(image)))
    else:
        raise IconRefused(IconProblem.FORMAT)
    if len(png) > MAX_ICON_SIZE:
        raise IconRefused(IconProblem.SIZE)
    return png


def png_size(png: bytes) -> tuple[int, int]:
    """Return a PNG image's width and height in pixels, as its header chunk gives them.

    Args:
        png: The image, which icon_png accepted.
    """
    width, height = struct.unpack_from(">II", png, 16)
    return int(width), int(height)


def _is_png(data: bytes) -> bool:
    # The signature, then the header chunk that holds the image's size.
    return len(data) >= 24 and data.startswith(PNG_SIGNATURE) and data[12:16] == b"IHDR"


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
