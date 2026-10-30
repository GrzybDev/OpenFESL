from datetime import UTC, datetime
from typing import Any

from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Games
from openfesl.core.inventory import Ownership
from openfesl.core.texts import localize
from openfesl.web.store_texts import StoreTexts


async def price_entry(
    details: dict[str, Any], games: Games, catalogues: Catalogues, language: str
) -> tuple[str, str | None, str]:
    """Return how the audit log shows a change of an item's price points.

    Args:
        details: The change's details, as the admin's log keeps them.
        games: The games, whose stores the change was made in.
        catalogues: Name the items.
        language: The log's language.

    Returns:
        The item and its first changed product in words, the panel's page of the item and what
        changed in words.
    """
    game = games.by_id(details["game"])
    item = details["item"]
    if game is None or game.store is None:
        return str(item), None, ""
    catalogue = await catalogues.of(game, language)
    texts = StoreTexts(game, language, datetime.now(UTC))
    points = details["points"]
    shown = catalogue.name(item) or texts.text("web.admin.items.unnamed", id=item)
    if points:
        shown = f"{shown} · {game.store.product_id(points[0]['product'])}"
    said = []
    for point in points:
        named = texts.text(
            "web.admin.store.point.named",
            currency=texts.currency(point["currency"]),
            ownership=_owned(texts, point["ownership"]),
        )
        before, after = point["from"], texts.amount(point["to"])
        parts = []
        if before is None:
            parts.append(texts.text("web.admin.audit.price.added", price=after))
        elif before != point["to"]:
            before = texts.amount(before)
            parts.append(texts.text("web.admin.audit.price.changed", before=before, after=after))
        if point["from"] is not None and point["was"] != point["sold"]:
            parts.append(texts.text(f"web.admin.audit.price.{'on' if point['sold'] else 'off'}"))
        said.append(texts.text("web.admin.audit.price", point=named, change=", ".join(parts)))
    href = localize(f"/admin/store/{game.id}/items/{item}", language)
    return shown, href, "; ".join(said)


def rule_entry(details: dict[str, Any], games: Games, language: str) -> tuple[str, str | None, str]:
    """Return how the audit log shows a price rule that the admin changed and applied.

    Args:
        details: The change's details, as the admin's log keeps them.
        games: The games, whose stores the rule prices.
        language: The log's language.

    Returns:
        The rule in words, the panel's page of it and what changed in words.
    """
    game, key = games.by_id(details["game"]), details["rule"]
    if game is None:
        return key, None, ""
    texts = StoreTexts(game, language, datetime.now(UTC))
    after = details["after"]
    value = after["use_price"] or after["value"]
    said = texts.text(
        "web.admin.audit.rule",
        changed=texts.amount(details["changed"]),
        kept=texts.amount(details["kept"]),
        value=texts.amount(value),
        sold=", ".join(_owned(texts, written) for written in after["sold"])
        or texts.text("web.admin.store.rules.drops_only"),
    )
    href = localize(f"/admin/store/{game.id}/rules?rule={key}", language)
    return texts.text(f"games.{game.id}.price_rule.{key}"), href, said


def _owned(texts: StoreTexts, written: str) -> str:
    # An ownership as the log keeps it, such as 30d, in words.
    ownership = Ownership.read(written)
    assert isinstance(ownership, Ownership)
    return texts.ownership(ownership, inline=True)


async def box_entry(
    details: dict[str, Any], games: Games, catalogues: Catalogues, language: str
) -> tuple[str, str | None, str]:
    """Return how the audit log shows a box's drop table that the admin changed.

    Args:
        details: The change's details, as the admin's log keeps them.
        games: The games, whose stores the box belongs to.
        catalogues: Name the items.
        language: The log's language.

    Returns:
        The box's key, the panel's page of the game's boxes and what changed in words.
    """
    game = games.by_id(details["game"])
    if game is None:
        return details["box"], None, ""
    catalogue = await catalogues.of(game, language)
    texts = StoreTexts(game, language, datetime.now(UTC))

    def entries(listed: list[list[Any]]) -> str:
        return ", ".join(
            f"{catalogue.name(item) or item} ({_owned(texts, written)})" for item, written in listed
        )

    said = []
    if details["removed"]:
        said.append(texts.text("web.admin.audit.box.removed", entries=entries(details["removed"])))
    if details["added"]:
        said.append(texts.text("web.admin.audit.box.added", entries=entries(details["added"])))
    before, after = details["weights"]
    if before != after:
        said.append(
            texts.text(
                "web.admin.audit.box.weights",
                before=" / ".join(map(str, before)),
                after=" / ".join(map(str, after)),
            )
        )
    if details["worth"][0] != details["worth"][1]:
        said.append(
            texts.text(f"web.admin.audit.box.worth.{'on' if details['worth'][1] else 'off'}")
        )
    href = localize(f"/admin/store/{game.id}/boxes", language)
    return details["box"], href, "; ".join(said)
