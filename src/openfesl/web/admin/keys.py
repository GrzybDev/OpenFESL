from dataclasses import dataclass

from openfesl.core.entitlements import StoredKey
from openfesl.core.games import Game, Games


@dataclass(frozen=True, slots=True)
class KeyView:
    """A product key as the admin panel shows it.

    Attributes:
        key: The key.
        game: The game whose data lists it among its starting keys, if any.
    """

    key: StoredKey
    game: Game | None


def key_views(games: Games, keys: list[StoredKey]) -> list[KeyView]:
    """Return the views of product keys, each with the game that lists it, in their order.

    Args:
        games: The games the server serves.
        keys: The keys.
    """
    starting = {key.code.upper(): game for game in games for key in game.keys}
    return [KeyView(key, starting.get(key.code.upper())) for key in keys]
