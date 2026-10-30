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
        ownership = Ownership.read(point["ownership"])
        assert isinstance(ownership, Ownership)
        named = texts.text(
            "web.admin.store.point.named",
            currency=texts.currency(point["currency"]),
            ownership=texts.ownership(ownership, inline=True),
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
