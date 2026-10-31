from datetime import UTC, datetime
from typing import Any

from openfesl.core.admin import AdminAction
from openfesl.core.catalogues import Catalogues, game_text
from openfesl.core.games import Games
from openfesl.core.inventory import Ownership
from openfesl.core.texts import localize
from openfesl.web.admin.store_account import CHESTS
from openfesl.web.pages import moment
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


def account_entry(
    details: dict[str, Any], games: Games, language: str
) -> tuple[str, str | None, str]:
    """Return how the audit log shows an account price, allowance or cap that the admin changed.

    Args:
        details: The change's details, as the admin's log keeps them.
        games: The games, whose values the change was made in.
        language: The log's language.

    Returns:
        The value in words, the panel's page of the account values and what changed in words.
    """
    game = games.by_id(details["game"])
    if game is None:
        return details["name"], None, ""
    texts = StoreTexts(game, language, datetime.now(UTC))
    unit = details["unit"]
    named = (
        texts.text("web.admin.store.account.unit.chests")
        if unit == CHESTS
        else texts.currency(unit)
    )
    shown = texts.text(
        f"web.admin.store.account.{details['name'].partition('-')[0]}", currency=named
    )
    said = texts.text(
        "web.admin.audit.store_account",
        before=texts.amount(details["before"]),
        after=texts.amount(details["after"]),
        unit=named,
    )
    return shown, localize(f"/admin/store/{game.id}/account", language), said


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


def deal_entry(
    action: AdminAction, target: str, details: dict[str, Any], games: Games, language: str
) -> tuple[str, str | None, str]:
    """Return how the audit log shows a change of a game's hot-deal rotation.

    Args:
        action: What the admin did: switched the rotation or changed a section.
        target: The game, or the game and the section as game:section.
        details: The change's details, as the admin's log keeps them.
        games: The games, whose rotations the change was made in.
        language: The log's language.

    Returns:
        The rotation or section in words, the panel's page of it and what changed in words.
    """
    game = games.by_id(target.partition(":")[0])
    if game is None:
        return target, None, ""
    texts = StoreTexts(game, language, datetime.now(UTC))
    href = localize(f"/admin/store/{game.id}/deals", language)
    if action is AdminAction.DEAL_ROTATION:
        return texts.text("web.admin.deals.rotation"), href, ""
    key = details["section"]
    before, after = details["before"], details["after"]
    said = []
    for name in after:
        if before[name] != after[name]:
            said.append(
                texts.text(
                    f"web.admin.audit.deal.{name}",
                    before=_deal_value(texts, before[name]),
                    after=_deal_value(texts, after[name]),
                )
            )
    name = texts.text(f"games.{game.id}.deal_section.{key}")
    return name, localize(f"/admin/store/{game.id}/deals?section={key}", language), "; ".join(said)


def _deal_value(texts: StoreTexts, value: Any) -> str:
    # A section's value as the log says it: on or off, a number, or a range of discounts.
    if isinstance(value, bool):
        return texts.text(f"web.admin.deals.switch.{'runs' if value else 'off'}")
    if isinstance(value, list):
        return texts.text("web.admin.deals.discount", low=value[0], high=value[1])
    return texts.amount(value) if value is not None else texts.text("web.admin.deals.size.all")


async def offer_entry(
    action: AdminAction,
    target: str,
    details: dict[str, Any],
    games: Games,
    catalogues: Catalogues,
    language: str,
) -> tuple[str, str, str | None, str]:
    """Return how the audit log shows an admin offer that the admin saved or ended.

    Args:
        action: What the admin did: saved or ended the offer.
        target: The game and the offer's bundle number, as game:number.
        details: The change's details, as the admin's log keeps them.
        games: The games, whose hot deals list the offer.
        catalogues: Name the offers that take the game's texts.
        language: The log's language.

    Returns:
        The kind of the action, the offer in words, the panel's page of the offer and what it
        sells for and when, or the end it had, in words.
    """
    game = games.by_id(details["game"])
    if game is None:
        return action.value, details["name"], None, ""
    catalogue = await catalogues.of(game, language)
    shown = game_text(game, catalogue, details["name"], language, details["faction"])
    href = localize(f"/admin/store/{target.replace(':', '/deals/offers/', 1)}", language)
    texts = StoreTexts(game, language, datetime.now(UTC))

    def written(at: str) -> str:
        return moment(datetime.fromisoformat(at).replace(tzinfo=UTC), language)

    if action is AdminAction.DEAL_ENDED:
        ends = details["ends"]
        said = texts.text("web.admin.audit.deal.ended", ends=written(ends)) if ends else ""
        return action.value, shown, href, said
    said = texts.text(
        "web.admin.audit.deal.offer",
        section=texts.text(f"games.{game.id}.deal_section.{details['section']}"),
        price=texts.priced(details["price"], details["currency"]),
        starts=written(details["starts"]),
        ends=written(details["ends"]),
    )
    return f"deal_offer{'.new' if details['new'] else ''}", shown, href, said


async def art_entry(
    target: str, details: dict[str, Any], games: Games, catalogues: Catalogues, language: str
) -> tuple[str, str, str | None, str]:
    """Return how the audit log shows a hot deal's picture that the admin uploaded or removed.

    Args:
        target: The game's id.
        details: The change's details, as the admin's log keeps them.
        games: The games, whose hot deals the pictures show.
        catalogues: Name the offers.
        language: The log's language.

    Returns:
        The kind of the action, the offer in words, the panel's page of the hot deals and which
        picture was uploaded in words.
    """
    game = games.by_id(target)
    offer = details["offer"]
    if game is None:
        return "deal_art", offer, None, ""
    shown = game_text(game, await catalogues.of(game, language), offer, language)
    href = localize(f"/admin/store/{game.id}/deals#home", language)
    if details.get("removed"):
        return "deal_art.removed", shown, href, ""
    texts = StoreTexts(game, language, datetime.now(UTC))
    picture = "big" if details["big"] else "small"
    return "deal_art", shown, href, texts.text(f"web.admin.audit.deal_art.{picture}")
