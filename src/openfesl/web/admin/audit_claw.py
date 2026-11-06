from typing import Any

from openfesl.core.admin import AdminAction
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.texts import localize
from openfesl.web.admin.audit_store import box_entry
from openfesl.web.admin.claw_panel import claw_name
from openfesl.web.store_texts import StoreTexts


async def claw_entry(
    action: AdminAction,
    target: str,
    details: dict[str, Any],
    games: Games,
    catalogues: Catalogues,
    language: str,
) -> tuple[str, str, str | None, str]:
    """Return how the audit log shows a change of a game's Daily Claw.

    Args:
        action: What the admin did: changed its settings or its prizes.
        target: The game, or the game and the Claw's box as game:box.
        details: The change's details, as the admin's log keeps them.
        games: The games.
        catalogues: Name the Claw and its prizes.
        language: The log's language.

    Returns:
        The kind of the action, what it was done to in words, the panel's page of it and what
        changed in words; the note follows them.
    """
    game = games.by_id(target.partition(":")[0])
    if game is None or game.claw is None:
        return action.value, target, None, ""
    texts = StoreTexts(game, language)
    name = claw_name(game, await catalogues.of(game, language), texts)
    if action is AdminAction.CLAW_TABLE:
        _, href, said = await box_entry(details, games, catalogues, language)
        before, after = details["rare"]
        if before != after:
            marks = {"before": _tiers(texts, game, before), "after": _tiers(texts, game, after)}
            rare = texts.text("web.admin.audit.claw.rare", **marks)
            said = "; ".join(filter(None, [said, rare]))
        shown = texts.text("web.admin.boxes.claw.pool.title", name=name)
        return action.value, shown, href, said
    href = localize(f"/admin/store/{game.id}/boxes#boxes-claw", language)
    changes = {field: (before, after) for field, before, after in details["changes"]}
    kind = action.value
    if "runs" in changes:
        kind += ".on" if changes["runs"][1] else ".off"
    lines: list[str] = []
    for field, (was, now) in changes.items():
        if field != "runs":
            values = {"before": _value(texts, field, was), "after": _value(texts, field, now)}
            lines.append(texts.text(f"web.admin.audit.claw.{field}", **values))
    return kind, name, href, "; ".join(lines)


def _value(texts: StoreTexts, field: str, value: str) -> str:
    # A setting's value as the log writes it: yes or no, a currency's name, or a number.
    if field in ("paid", "feed"):
        return texts.text("web.admin.boxes.claw.yes" if value else "web.admin.boxes.claw.no")
    if field == "currency":
        return texts.currency(value)
    return texts.amount(int(value))


def _tiers(texts: StoreTexts, game: Game, tiers: list[int]) -> str:
    # The names of tiers, or none.
    assert game.claw is not None
    names = [
        texts.text(f"games.{game.id}.claw.tier.{game.claw.tiers[tier].name}")
        if tier < len(game.claw.tiers) and game.claw.tiers[tier].name
        else texts.text("web.admin.boxes.tier", number=tier + 1)
        for tier in tiers
    ]
    return ", ".join(names) or texts.text("web.admin.audit.nothing")
