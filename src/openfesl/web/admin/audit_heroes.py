from datetime import datetime
from typing import Any

from openfesl.core.admin import AdminAction
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Games
from openfesl.core.personas import Persona
from openfesl.core.texts import translate
from openfesl.web.pages import moment
from openfesl.web.pages import number as grouped


def hero_entry(action: AdminAction, details: dict[str, Any], owner: str, language: str) -> str:
    """Return what the audit log says of a hero that the admin renamed or made the default.

    Args:
        action: What the admin did: renamed the hero or made it the account's default.
        details: The change's details, as the admin's log keeps them.
        owner: The hero's account in words, which the log names when nothing else changed.
        language: The log's language.
    """
    if action is AdminAction.HERO_RENAME:
        names = {"old": details["from"], "new": details["to"]}
        return translate("web.admin.audit.renamed", language) % names
    if details.get("was"):
        return translate("web.admin.audit.default_was", language) % {"hero": details["was"]}
    return owner


async def item_entry(
    action: AdminAction,
    details: dict[str, Any],
    persona: Persona,
    games: Games,
    catalogues: Catalogues,
    language: str,
) -> str:
    """Return what the audit log says of an item that the admin granted a hero, changed or revoked.

    Args:
        action: What the admin did: granted the item, changed how it is held or revoked it.
        details: The change's details, as the admin's log keeps them.
        persona: The hero's persona.
        games: The games, one of which the hero plays.
        catalogues: Name the items.
        language: The log's language.
    """
    played = next(game for game in games if game.namespace == persona.namespace)
    catalogue = await catalogues.of(played, language)
    item = details["item"]
    name = catalogue.name(item) or translate("web.admin.items.unnamed", language) % {"id": item}
    if action is AdminAction.ITEM_REVOKE:
        return name
    held = _held(details, language)
    if action is AdminAction.ITEM_CHANGE:
        changed = {"before": _held(details["before"], language), "after": held}
        held = translate("web.admin.audit.changed", language) % changed
    return translate("web.admin.audit.item", language) % {"item": name, "held": held}


def slot_entry(details: dict[str, Any], language: str) -> str:
    """Return what the audit log says of hero slots that the admin granted an account.

    Args:
        details: The grant's details, as the admin's log keeps them.
        language: The log's language.
    """
    count, granted = grouped(details["count"], language), grouped(details["granted"], language)
    return translate("web.admin.audit.slots", language) % {"count": count, "granted": granted}


def count_entry(action: AdminAction, details: dict[str, Any], language: str) -> tuple[str, str]:
    """Return how the audit log shows a grant or take of a hero's chests or an account's keys.

    Args:
        action: What the admin changed: a hero's chests or an account's keys.
        details: The change's details, as the admin's log keeps them; a take counts below 0.
        language: The log's language.

    Returns:
        The kind of the action and what changed in words.
    """
    count, before = details["count"], details["before"]
    counts = {
        "count": grouped(abs(count), language),
        "before": grouped(before, language),
        "after": grouped(before + count, language),
    }
    kind = f"{action.value}.{'taken' if count < 0 else 'granted'}"
    return kind, translate("web.admin.audit.counted", language) % counts


def _held(values: dict[str, Any], language: str) -> str:
    # How a hero holds an item, as the log keeps it.
    if values.get("uses") is not None:
        uses = grouped(values["uses"], language)
        return translate("web.admin.audit.uses", language) % {"count": uses}
    if values.get("expires"):
        day = moment(datetime.fromisoformat(values["expires"]), language)
        return translate("web.admin.audit.ends", language) % {"time": day}
    return translate("web.store.price.permanent", language)
