import logging
from collections.abc import Callable
from typing import Protocol

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.admins import Admin
from openfesl.core.games import Game

logger = logging.getLogger(__name__)


class MarkStore(Protocol):
    """Where the choice of whether each game shows its own server mark is kept, each recorded."""

    async def hidden_marks(self) -> set[str]:
        """Return the ids of the games whose own server mark an admin turned off."""

    async def show_mark(self, game: str, shown: bool, official: int, admin: Admin) -> None:
        """Keep whether a game shows its own server mark and record the change.

        Args:
            game: The game's id.
            shown: Whether official servers publish the mark.
            official: How many official servers host the game now, as the log records.
            admin: The admin who changed it.
        """


class GameMarks:
    """Whether each game shows its own server mark at official servers, held in memory.

    A game whose data names a mark attribute shows it by default; while an admin keeps it off,
    every server of the game publishes 0, whatever its own official mark.
    """

    def __init__(self, store: MarkStore) -> None:
        """Keep the choices in a store.

        Args:
            store: Where the choices are kept.
        """
        self._store = store
        self._hidden: set[str] = set()
        self._changed: list[Callable[[str], object]] = []

    async def load(self) -> None:
        """Read every game's choice, as when the server starts."""
        self._hidden = await self._store.hidden_marks()

    def follow(self, feed: AdminFeed) -> None:
        """Take a change made elsewhere as soon as the log records it.

        Args:
            feed: The admin's actions as the log records them.
        """
        feed.on(AdminAction.GAME_MARK, self._switched)

    def on_change(self, listener: Callable[[str], object]) -> None:
        """Call a function after a game starts or stops showing its own server mark.

        Args:
            listener: Called with the game's id.
        """
        self._changed.append(listener)

    def shown(self, game: Game) -> bool:
        """Return whether a game's official servers publish its own server mark.

        Args:
            game: The game.
        """
        return game.id not in self._hidden

    async def show(self, game: Game, shown: bool, official: int, admin: Admin) -> None:
        """Change whether a game shows its own server mark, at once on this server.

        Args:
            game: The game.
            shown: Whether official servers publish the mark from now on.
            official: How many official servers host the game now, as the log records.
            admin: The admin who changed it.
        """
        await self._store.show_mark(game.id, shown, official, admin)
        self._set(game.id, shown)

    def _switched(self, action: LoggedAction) -> None:
        shown = action.details.get("shown")
        if not isinstance(shown, bool):
            logger.warning("Ignoring the server mark change %d, which says nothing", action.id)
            return
        self._set(action.target, shown)

    def _set(self, game: str, shown: bool) -> None:
        if shown == (game not in self._hidden):
            return
        if shown:
            self._hidden.discard(game)
        else:
            self._hidden.add(game)
        for listener in self._changed:
            listener(game)
