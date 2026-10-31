from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from openfesl.core.admin import AdminAction
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Games
from openfesl.core.reward_rules import Gift
from openfesl.core.texts import localize
from openfesl.web.admin.gift_codes import CODES
from openfesl.web.store_texts import StoreTexts


async def code_entry(
    action: AdminAction,
    target: str,
    details: dict[str, Any],
    games: Games,
    catalogues: Catalogues,
    language: str,
) -> tuple[str, str, str, str]:
    """Return how the audit log shows an action on a code that gives a gift.

    Args:
        action: What the admin did.
        target: The code.
        details: What the action records.
        games: The games, whose codes they are.
        catalogues: The games' imported catalogues, which name the items.
        language: The log's language.

    Returns:
        The kind of action, what it was done to, the address of its page and what changed.
    """
    game = games.by_id(details["game"])
    href = localize(f"{CODES}/{quote(target)}", language)
    if game is None:
        return action.value, target, href, ""
    texts = StoreTexts(game, language, datetime.now(UTC))
    catalogue = await catalogues.of(game, language)

    def gift(written: dict[str, Any]) -> str:
        words = texts.gift_words(catalogue, Gift.model_validate(written))
        return " · ".join(words) or texts.text("web.admin.audit.nothing")

    def until(written: str | None) -> str:
        if written is None:
            return texts.text("web.admin.keys.no_end")
        return texts.moment(datetime.fromisoformat(written))

    def most(value: int | None) -> str:
        if value is None:
            return texts.text("web.admin.audit.limits.any")
        return texts.text("web.admin.audit.limits.most", most=texts.amount(value))

    said = [
        details["label"],
        gift(details["gift"]),
        most(details["max_uses"]),
        until(details["expires"]),
    ]
    return "key_add.code", target, href, " · ".join(said)
