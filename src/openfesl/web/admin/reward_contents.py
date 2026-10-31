from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogue
from openfesl.core.drops import Box
from openfesl.core.reward_rules import Event, Gift, Part
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.pages import whole_number
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class PartDraft:
    """A part of an event in its editor as written: whether it is on and what it gives.

    Attributes:
        key: Its key in the event.
        on: Whether it is given.
        items: Its items, each written such as 1127 7d.
        box: The key of the box it rolls; empty for none.
        funds: The Funds it gives, as written.
        keys: The treasure chest keys it gives, as written.
        days: The items and the box it gives instead on some days, by the day from 1.
        goal: What one hero's count must reach first, as written; empty for no goal.
    """

    key: str
    on: bool
    items: tuple[str, ...]
    box: str
    funds: str
    keys: str
    days: dict[int, tuple[tuple[str, ...], str]]
    goal: str


@dataclass(frozen=True, slots=True)
class Adding:
    """An item that the add panel adds to a part of an event, as written.

    Attributes:
        item: The item's number.
        part: The key of the part.
        day: The day from 1 whose gift it joins; empty for the part's own gift.
        ownership: perm, a number of days such as 7d, or uses.
        uses: How many uses an item owned by uses brings.
    """

    item: str
    part: str
    day: str
    ownership: str
    uses: str


@dataclass(frozen=True, slots=True)
class Contents:
    """What an event's parts give as its editor sets them.

    Attributes:
        drafts: The parts' fields, with an added item among them.
        parts: The event's parts as edited, a refused amount as 0.
        refused: Why a field is refused, by its name.
        added: The item that was added and where, in words; empty for none.
    """

    drafts: tuple[PartDraft, ...]
    parts: dict[str, Part]
    refused: dict[str, str]
    added: str


def drafted_parts(event: Event) -> tuple[PartDraft, ...]:
    """Return the editor's fields of an event's parts as the instance sets them.

    Args:
        event: The event.
    """
    return tuple(
        PartDraft(
            key,
            part.enabled,
            _written(part.gift),
            part.gift.box or "",
            str(part.gift.funds),
            str(part.gift.keys),
            {day: (_written(gift), gift.box or "") for day, gift in part.days.items()},
            str(part.goal.count) if part.goal else "",
        )
        for key, part in event.parts.items()
    )


def written_parts(fields: ImmutableMultiDict[str, Any], event: Event) -> tuple[PartDraft, ...]:
    """Return the editor's fields of an event's parts as a form wrote them.

    Args:
        fields: The form's fields; a form without the parts leaves them as the event sets them.
        event: The event.
    """
    if "parts" not in fields:
        return drafted_parts(event)
    dropped = set(fields.getlist("drop-day"))
    found = []
    for key in event.parts:
        removed = set(fields.getlist(f"drop-{key}"))
        items = fields.getlist(f"item-{key}")
        prefix = f"day-box-{key}-"
        days = {
            int(number): (tuple(fields.getlist(f"day-{key}-{number}")), str(fields[name]))
            for name in fields
            if name.startswith(prefix)
            and (number := name.removeprefix(prefix)).isdecimal()
            and number not in dropped
        }
        found.append(
            PartDraft(
                key,
                bool(fields.get(f"on-{key}")),
                tuple(text for index, text in enumerate(items) if str(index) not in removed),
                str(fields.get(f"box-{key}", "")),
                str(fields.get(f"funds-{key}", "")).strip(),
                str(fields.get(f"keys-{key}", "")).strip(),
                days,
                str(fields.get(f"goal-{key}", "")).strip(),
            )
        )
    return tuple(found)


def written_adding(fields: Mapping[str, Any]) -> Adding | None:
    """Return the item that a form's add panel adds, if its Add button sent the form.

    Args:
        fields: The form's fields.
    """
    item = str(fields.get("add", ""))
    if not item:
        return None
    return Adding(
        item,
        str(fields.get(f"to-{item}", "")),
        str(fields.get(f"add-day-{item}", "")).strip(),
        str(fields.get(f"own-{item}", "")),
        str(fields.get(f"uses-{item}", "")).strip(),
    )


def contents_of(
    texts: StoreTexts,
    catalogue: Catalogue,
    event: Event,
    drafts: tuple[PartDraft, ...],
    adding: Adding | None,
    boxes: Mapping[str, Box],
    days: int,
) -> Contents:
    """Return what an event's parts give as its editor's fields set them, adding an item.

    Args:
        texts: Writes the page's language.
        catalogue: The game's imported catalogue, which tells whom an item fits.
        event: The event as the instance sets it.
        drafts: The editor's fields of its parts.
        adding: The item the add panel adds, if it sent the form.
        boxes: The boxes that the store may roll for a reward, by key.
        days: How many days the event runs, which an added item's day falls within.

    Raises:
        HTTPException: 400 for fields that no form of the editor sends.
    """
    refused: dict[str, str] = {}
    title = texts.text(event.title)
    added = ""
    if adding:
        drafts, added = _add(texts, catalogue, event, drafts, adding, days, refused)
    parts = {}
    for draft in drafts:
        part = event.parts[draft.key]
        # The account's own part rolls no box, as no hero is chosen for it.
        rolled = {draft.box, *(box for _, box in draft.days.values())} - {""}
        fitting = boxes.items() if part.faction else ()
        allowed = {key for key, box in fitting if box.faction in (None, part.faction)}
        if rolled - allowed:
            raise HTTPException(400)
        amounts = [_amount(draft.funds), _amount(draft.keys)]
        for name, amount in zip(("funds", "keys"), amounts, strict=True):
            if amount is None:
                refused[f"{name}-{draft.key}"] = texts.text(
                    "web.admin.rewards.refused.amount", most=texts.amount(MOST_BALANCE)
                )
        try:
            gift = Gift.model_validate(
                {
                    "items": draft.items,
                    "box": draft.box or None,
                    "funds": amounts[0] or 0,
                    "keys": amounts[1] or 0,
                }
            )
            others = {
                day: Gift.model_validate({"items": items, "box": box or None})
                for day, (items, box) in draft.days.items()
            }
        except ValidationError:
            raise HTTPException(400) from None
        goal = part.goal
        if goal:
            count = _amount(draft.goal)
            if not count:
                refused[f"goal-{draft.key}"] = texts.text(
                    "web.admin.rewards.refused.goal", most=texts.amount(MOST_BALANCE)
                )
            goal = goal.model_copy(update={"count": count or goal.count})
        if draft.on and not (gift.for_hero or gift.funds or gift.keys):
            key = "empty" if part.faction else "empty.account"
            faction = texts.factions(frozenset({part.faction})) if part.faction else ""
            refused[f"part-{draft.key}"] = texts.text(
                f"web.admin.rewards.refused.{key}", faction=faction, event=title
            )
        parts[draft.key] = part.model_copy(
            update={"enabled": draft.on, "gift": gift, "days": others, "goal": goal}
        )
    return Contents(drafts, parts, refused, added)


def _add(
    texts: StoreTexts,
    catalogue: Catalogue,
    event: Event,
    drafts: tuple[PartDraft, ...],
    adding: Adding,
    days: int,
    refused: dict[str, str],
) -> tuple[tuple[PartDraft, ...], str]:
    # The parts' fields with the item added, and what was added in words; a refusal leaves
    # them as they are.
    part = event.parts.get(adding.part)
    item = catalogue.item(int(adding.item)) if adding.item.isdecimal() else None
    rentals = {f"{days}d" for days in texts.game.store.rentals} if texts.game.store else set()
    if part is None or part.faction is None or item is None or item.withheld:
        raise HTTPException(400)
    if adding.ownership not in {"perm", "uses", *rentals}:
        raise HTTPException(400)
    uses = whole_number(adding.uses, MOST_BALANCE) or 0
    name = catalogue.name(item.id) or str(item.id)
    faction = texts.factions(frozenset({part.faction}))
    day = int(adding.day) if adding.day.isascii() and adding.day.isdecimal() else None
    if part.faction not in item.factions:
        refused["add"] = texts.text(
            "web.admin.rewards.refused.faction",
            item=name,
            others=texts.factions(item.factions),
            faction=faction,
            event=texts.text(event.title),
        )
    elif adding.ownership == "uses" and not 0 < uses <= MOST_BALANCE:
        refused["add"] = texts.text(
            "web.admin.rewards.refused.uses", most=texts.amount(MOST_BALANCE)
        )
    elif adding.day and not (day and day <= days and event.daily):
        refused["add"] = texts.text(
            "web.admin.rewards.refused.add_day", day=adding.day, days=texts.amount(days)
        )
    if "add" in refused:
        return drafts, ""
    written = f"{item.id} {f'x{uses}' if adding.ownership == 'uses' else adding.ownership}"
    found = []
    for draft in drafts:
        if draft.key == adding.part and day:
            # A new day keeps the part's box for the heroes whom the item does not fit.
            items, box = draft.days.get(day, ((), draft.box))
            draft = replace(draft, days=draft.days | {day: ((*items, written), box)})
        elif draft.key == adding.part:
            draft = replace(draft, items=(*draft.items, written))
        found.append(draft)
    where = texts.text("web.admin.rewards.event.day", day=day) if day else ""
    added = texts.text(
        "web.admin.rewards.added.day" if day else "web.admin.rewards.added",
        item=name,
        faction=faction,
        day=where,
    )
    return tuple(found), added


def gift_json(gift: Gift) -> dict[str, Any]:
    """Return a gift as the setting that overrides it holds it, its items written such as 1127 7d.

    Args:
        gift: The gift.
    """
    found = {"items": _written(gift), "box": gift.box, "funds": gift.funds, "keys": gift.keys}
    return {name: value for name, value in found.items() if value}


def _written(gift: Gift) -> tuple[str, ...]:
    # A gift's items as the editor writes them, such as 1127 7d.
    return tuple(f"{item_id} {ownership.written}" for item_id, ownership in gift.items)


def _amount(text: str) -> int | None:
    # A number of Funds or keys from 0 as the form writes it; any other text counts as none.
    return whole_number(text or "0", MOST_BALANCE, 0)
