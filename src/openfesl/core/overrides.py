import json
import logging
from collections.abc import Mapping

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


def overridden[Rules: BaseModel](rules: Rules, prefix: str, settings: Mapping[str, str]) -> Rules:
    """Return rules of a game's data with the instance's settings over its defaults.

    A setting names a field by its path after the prefix, such as `sections.sale.enabled`; one
    that names no field, or a value the field does not take, is skipped. A value that is a JSON
    list or object, such as `["perm", "30d"]`, sets a field of lists or tables.

    Args:
        rules: The rules as the game's data gives them.
        prefix: The start of the keys of the settings that override them, such as
            `bfheroes.deals.`.
        settings: The settings, by key.
    """
    for key, value in sorted(settings.items()):
        *path, name = key.removeprefix(prefix).split(".")
        data = rules.model_dump()
        node = data
        try:
            for part in path:
                node = node[part]
            if not isinstance(node, dict) or name not in node:
                raise KeyError(name)
            node[name] = _read(value)
            rules = rules.model_validate(data)
        except KeyError, TypeError, ValidationError:
            logger.warning("Ignoring the setting %s, which no rule takes", key)
    return rules


def _read(value: str) -> object:
    # A list or table written as JSON; any other value as its text, which the field reads.
    try:
        read = json.loads(value)
    except ValueError:
        return value
    return read if isinstance(read, list | dict) else value
