from typing import Any

from openfesl.core.admin import AdminAction
from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.games import Games
from openfesl.core.reward_rules import Gift
from openfesl.core.texts import BASE_LANGUAGE, localize, translate
from openfesl.web.admin.referral_editor import SIDES
from openfesl.web.referral_texts import NUMBERS
from openfesl.web.store_texts import StoreTexts


async def referral_entry(
    action: AdminAction,
    target: str,
    details: dict[str, Any],
    games: Games,
    catalogues: Catalogues,
    language: str,
) -> tuple[str, str, str | None, str]:
    """Return how the audit log shows a change of Recruit a Friend.

    Args:
        action: What the admin did: switched it, changed a step or removed a recruit.
        target: The game's id, or the removed recruit's account.
        details: The change's details, as the admin's log keeps them.
        games: The games.
        catalogues: The games' imported catalogues, which name the items.
        language: The log's language.

    Returns:
        The kind of the action, what it was done to in words, the panel's page of it and what
        changed in words; the note follows them.
    """
    title = translate("web.referral.title", language)
    if action is AdminAction.REFERRAL_REMOVED:
        recruit, recruiter = details["names"]
        shown = translate("web.admin.audit.referral.recruit", language) % {
            "recruit": recruit,
            "name": recruiter,
        }
        return action.value, shown, localize(f"/admin/accounts/{target}", language), ""
    href = localize(f"/admin/rewards/{target}#rewards-referral", language)
    if "enabled" in details:
        return f"{action.value}.{'on' if details['enabled'] else 'off'}", title, href, ""
    game = games.by_id(target)
    before, after = details["before"], details["after"]
    said = []
    for name in ("level", "days"):
        if before[name] != after[name]:
            values = {name: before[name] or 0, "before": before[name], "after": after[name]}
            said.append(translate(f"web.admin.audit.referral.{name}", language) % values)
    for side in SIDES:
        was, now = before[side], after[side]
        if was != now and game:
            texts = StoreTexts(game, language)
            catalogue = await catalogues.of(game, language)
            values = {"before": _said(texts, catalogue, was), "after": _said(texts, catalogue, now)}
            said.append(translate(f"web.admin.audit.referral.{side}_gift", language) % values)
    step = translate("web.referral.step", language) % {"n": NUMBERS[details["step"]]}
    return action.value, f"{title} · {step}", href, "; ".join(said)


def _said(texts: StoreTexts, catalogue: Catalogue, side: dict[str, Any]) -> str:
    # A side as the audit log writes it: its gift and the admin's English name, if it has one.
    words = texts.gift_words(catalogue, Gift.model_validate(side["gift"]))
    if own := side["own_text"].get(BASE_LANGUAGE):
        words.append(texts.text("web.admin.audit.referral.own", text=own))
    return ", ".join(words)
