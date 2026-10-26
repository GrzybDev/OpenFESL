from collections.abc import Sequence
from dataclasses import dataclass, field

from openfesl.core.entitlements import KeyHistory, StoredKey
from openfesl.core.games import Game, Games
from openfesl.core.texts import translate


@dataclass(frozen=True, slots=True)
class KeyView:
    """A product key as the admin panel and the guides show it.

    Attributes:
        key: The key.
        game: The game whose players it is for, when known.
        starting: Whether the game's data lists it among its starting keys.
        history: When the admin added it and last enabled or disabled it.
    """

    key: StoredKey
    game: Game | None
    starting: bool = False
    history: KeyHistory = field(default_factory=KeyHistory)

    @property
    def tab(self) -> str:
        """The tab of the keys page that lists it besides all: enabled or disabled."""
        return "enabled" if self.key.enabled else "disabled"

    def label(self, language: str) -> str | None:
        """Return what the public pages call the key in a language, if anything.

        Args:
            language: The language code.
        """
        return self.key.labels.get(language) or self.fallback(language)

    def fallback(self, language: str) -> str | None:
        """Return the label a starting key has in its game's texts when the admin set none.

        Args:
            language: The language code.
        """
        if self.starting and self.game is not None:
            return translate(f"games.{self.game.id}.key.{self.key.code}", language)
        return None


def key_views(
    games: Games, keys: Sequence[StoredKey], history: dict[str, KeyHistory] | None = None
) -> list[KeyView]:
    """Return the views of product keys, each with the game that lists it, in their order.

    Args:
        games: The games the server serves.
        keys: The keys.
        history: When the admin added and changed them, by code.
    """
    starting = {key.code.upper(): game for game in games for key in game.keys}
    known = history or {}
    views = []
    for key in keys:
        listed = starting.get(key.code.upper())
        game = listed or (games.by_id(key.game) if key.game else None)
        views.append(KeyView(key, game, listed is not None, known.get(key.code, KeyHistory())))
    return views
