import logging
from typing import Protocol

from openfesl.core.games import Game
from openfesl.core.magma import VALIDATION_FAILED, MagmaError
from openfesl.core.personas import Persona
from openfesl.formats import png

logger = logging.getLogger(__name__)

# The limits of a mugshot: the longest side, as 1.89 renders its heroes 1024 pixels square
# (mods/bfheroes/mugshot.png, about 200 KB), and the file.
LONGEST_SIDE = 1024
LARGEST_FILE = 1024 * 1024

# The cause of an upload that is no mugshot.
NOT_A_MUGSHOT = "NOT_A_MUGSHOT"


class MugshotStore(Protocol):
    """Where the mugshots of heroes are kept, one for each hero."""

    async def replace(self, persona_id: int, image: bytes) -> int:
        """Keep a hero's new mugshot in place of the one before.

        Args:
            persona_id: The hero.
            image: The mugshot as a PNG picture.

        Returns:
            The mugshot's version, which each different picture raises.
        """

    async def image(self, persona_id: int, namespace: str) -> tuple[bytes, int] | None:
        """Return a hero's mugshot as a PNG picture with its version, if its game uploaded one.

        Args:
            persona_id: The hero.
            namespace: The namespace of its game's personas, which the hero must be in.
        """

    async def count(self, namespace: str) -> int:
        """Return how many heroes of a game have a mugshot.

        Args:
            namespace: The namespace of the game's personas.
        """


class Mugshots:
    """The mugshots that games render of their heroes and upload."""

    def __init__(self, store: MugshotStore) -> None:
        """Keep mugshots in a store.

        Args:
            store: Where the mugshots are kept.
        """
        self._store = store

    async def replace(self, persona: Persona, image: bytes) -> int:
        """Keep a hero's new mugshot, once it proves to be a PNG picture within the limits.

        Args:
            persona: The hero.
            image: The mugshot as its game uploaded it.

        Returns:
            The mugshot's version.

        Raises:
            MagmaError: When the upload is no PNG picture or too large for a mugshot.
        """
        try:
            if len(image) > LARGEST_FILE:
                raise ValueError(f"the file has {len(image)} bytes")
            width, height = png.size(image, LONGEST_SIDE)
        except ValueError as refused:
            logger.info("Refusing a mugshot of persona %d: %s", persona.id, refused)
            raise MagmaError(VALIDATION_FAILED, NOT_A_MUGSHOT) from None
        version = await self._store.replace(persona.id, image)
        logger.info("Persona %d has mugshot %d, %d x %d", persona.id, version, width, height)
        return version

    async def image(self, game: Game, persona_id: int) -> tuple[bytes, int] | None:
        """Return a hero's mugshot as a PNG picture with its version, if its game uploaded one.

        Args:
            game: The hero's game.
            persona_id: The hero.
        """
        return await self._store.image(persona_id, game.namespace)

    async def count(self, game: Game) -> int:
        """Return how many of a game's heroes have a mugshot.

        Args:
            game: The game.
        """
        return await self._store.count(game.namespace)
