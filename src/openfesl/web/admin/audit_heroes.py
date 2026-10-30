from typing import Any

from openfesl.core.admin import AdminAction
from openfesl.core.texts import translate


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
