from typing import Any

from pydantic import BaseModel

from openfesl.core.games import Game
from openfesl.core.overrides import overridden
from openfesl.core.policies import SettingStore


async def account_rules(game: Game, settings: SettingStore) -> Game:
    """Return a game with the instance's settings over its account prices, allowances and caps.

    The admin may change them while the server runs, so they are read on every use. A setting
    names a field of the game's heroes, chests or currencies by its path, such as
    bfheroes.heroes.slot_price, bfheroes.chests.most or bfheroes.currencies._PF.allowance.

    Args:
        game: The game, as its data gives it.
        settings: The instance's settings.
    """
    found = await settings.settings(f"{game.id}.")

    def part[Rules: BaseModel](rules: Rules, path: str) -> Rules:
        prefix = f"{game.id}.{path}."
        chosen = {key: value for key, value in found.items() if key.startswith(prefix)}
        return overridden(rules, prefix, chosen) if chosen else rules

    changes: dict[str, Any] = {
        "currencies": {
            code: part(currency, f"currencies.{code}") for code, currency in game.currencies.items()
        }
    }
    if game.heroes:
        changes["heroes"] = part(game.heroes, "heroes")
    if game.chests:
        changes["chests"] = part(game.chests, "chests")
    return game.model_copy(update=changes)
