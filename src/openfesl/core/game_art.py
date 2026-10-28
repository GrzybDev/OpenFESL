import logging
from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.admins import Admin
from openfesl.core.games import Game

logger = logging.getLogger(__name__)


class ArtViewers(StrEnum):
    """Who sees a game's art, which others see placeholders in place of.

    The art is the icons and hero models of the game's imported catalogue and the portraits that
    the game renders of its heroes.
    """

    EVERYONE = "everyone"
    """Every visitor."""
    SIGNED_IN = "signed_in"
    """Players and admins signed in, and the game through its own session; the default."""
    NOBODY = "nobody"
    """Nobody, admins included."""


class ArtStore(Protocol):
    """Where the choice of who sees each game's art is kept, each change recorded."""

    async def art_viewers(self) -> dict[str, ArtViewers]:
        """Return who sees each game's art, by the game's id, for the games an admin chose for."""

    async def choose_art_viewers(
        self, game: str, viewers: ArtViewers, covers: Mapping[str, int], admin: Admin | None
    ) -> None:
        """Keep who sees a game's art and record the choice.

        Args:
            game: The game's id.
            viewers: Who sees it.
            covers: How many files of each kind the choice covers now, as the log records them.
            admin: The admin who chose; None for the command line.
        """


class GameArt:
    """Who sees each game's art, held in memory and followed through the admin's log."""

    def __init__(self, store: ArtStore) -> None:
        """Keep the choices in a store.

        Args:
            store: Where the choices are kept.
        """
        self._store = store
        self._viewers: dict[str, ArtViewers] = {}

    async def load(self) -> None:
        """Read every game's choice, as when the server starts."""
        self._viewers = await self._store.art_viewers()

    def follow(self, feed: AdminFeed) -> None:
        """Take a choice that an admin made elsewhere as soon as the log records it.

        Args:
            feed: The admin's actions as the log records them.
        """
        feed.on(AdminAction.CATALOGUE_ART, self._chosen)

    def viewers(self, game: Game) -> ArtViewers:
        """Return who sees a game's art.

        Args:
            game: The game.
        """
        return self._viewers.get(game.id, ArtViewers.SIGNED_IN)

    def shown(self, game: Game, signed_in: bool) -> bool:
        """Return whether a game's art is shown to a viewer.

        Args:
            game: The game.
            signed_in: Whether the viewer is signed in, as a player, an admin or the game.
        """
        viewers = self.viewers(game)
        return viewers is ArtViewers.EVERYONE or (viewers is ArtViewers.SIGNED_IN and signed_in)

    async def choose(
        self, game: Game, viewers: ArtViewers, covers: Mapping[str, int], admin: Admin | None
    ) -> None:
        """Change who sees a game's art, at once on this server.

        Args:
            game: The game.
            viewers: Who sees it from now on.
            covers: How many files of each kind the choice covers now, as the log records them.
            admin: The admin who chose; None for the command line.
        """
        await self._store.choose_art_viewers(game.id, viewers, covers, admin)
        self._viewers[game.id] = viewers

    def _chosen(self, action: LoggedAction) -> None:
        try:
            self._viewers[action.target] = ArtViewers(action.details["viewers"])
        except KeyError, ValueError:
            logger.warning("Ignoring the art choice %d, which names no viewers", action.id)
