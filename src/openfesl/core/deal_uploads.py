from typing import Protocol

from openfesl.core.admins import Admin
from openfesl.core.store import Bundle


def offer_of(bundle: Bundle) -> str:
    """Return the name of the offer a hot deal sells, which its uploaded pictures go with.

    The rotation sells an offer again in later periods as new bundles of one name, so a picture
    uploaded for it stays.

    Args:
        bundle: The hot deal's bundle.
    """
    return bundle.pool or bundle.name


class DealUploads(Protocol):
    """Where the pictures that the admin uploaded for hot deals' offers are kept."""

    async def picture(self, game: str, offer: str, big: bool) -> bytes | None:
        """Return an offer's uploaded picture, if one was uploaded.

        Args:
            game: The game's data-file name.
            offer: The offer's name.
            big: Whether it is the big picture rather than the small one.
        """

    async def uploaded(self, game: str) -> dict[str, set[bool]]:
        """Return the offers of a game with uploaded pictures, each with which of them it has.

        Args:
            game: The game's data-file name.
        """

    async def put(self, game: str, offer: str, big: bool, image: bytes, admin: Admin) -> None:
        """Keep an offer's uploaded picture in place of its generated one, and record it.

        Args:
            game: The game's data-file name.
            offer: The offer's name.
            big: Whether it is the big picture rather than the small one.
            image: The picture, a PNG of the size the game shows.
            admin: The admin who uploaded it.
        """

    async def remove(self, game: str, offer: str, admin: Admin) -> bool:
        """Remove an offer's uploaded pictures, so the generated ones come back, and record it.

        Args:
            game: The game's data-file name.
            offer: The offer's name.
            admin: The admin who removed them.

        Returns:
            Whether it had any.
        """
