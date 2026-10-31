from dataclasses import dataclass, field
from typing import Any

from starlette.datastructures import FormData

from openfesl.core.catalogues import Item
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership
from openfesl.core.reward_rules import Gift, ReferralSide, ReferralStep
from openfesl.core.texts import BASE_LANGUAGE, languages
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.reward_contents import written_ownership
from openfesl.web.pages import whole_number
from openfesl.web.referral_texts import NUMBERS
from openfesl.web.reward_texts import RewardTexts

# The most days a rental or a step's window may last.
MOST_DAYS = 3650
SIDES = ("recruiter", "recruit")


@dataclass(frozen=True, slots=True)
class SideDraft:
    """A side of a step as the editor's fields write it.

    Attributes:
        kind: points, funds or item.
        amount: The points or Funds.
        item: The item's number.
        own: How the item is owned: perm, days or uses.
        count: Its days or uses.
        name: imported for the game's name, own for the admin's text.
        texts: The admin's name by language.
        query: The search for the item, a part of its name or its number.
    """

    kind: str
    amount: str
    item: str
    own: str
    count: str
    name: str
    texts: dict[str, str] = field(default_factory=dict)
    query: str = ""


@dataclass(frozen=True, slots=True)
class StepEditor:
    """A step's editor: its fields as written, why a save was refused, and what it says.

    Attributes:
        name: The step's name.
        number: The number players see it by.
        level: The hero level as written.
        days: The window in days as written; empty for the first step, which has none.
        sides: Each side's fields, by side.
        note: The note as written.
        refused: Why the save was refused, by field.
        imported: Each side's imported name and its text's id, where the game's texts have it.
        order: What the other step's goal asks of this one.
        points: Whether the rewards give points, which a gift may be.
        candidates: Each side's items that its search found, with their names, its chosen
            item first, by side.
        searchable: Whether the game's catalogue is imported, which the search finds items in.
        icons: Whether the catalogue holds the items' icons.
    """

    name: str
    number: int
    level: str
    days: str
    sides: dict[str, SideDraft]
    note: str = ""
    refused: dict[str, str] = field(default_factory=dict)
    imported: dict[str, tuple[str, str]] = field(default_factory=dict)
    order: str = ""
    points: bool = True
    candidates: dict[str, list[tuple[Item, str]]] = field(default_factory=dict)
    searchable: bool = False
    icons: bool = False


def drafted(side: ReferralSide) -> SideDraft:
    """Return a side of a step as the editor's fields write it.

    Args:
        side: The side as saved.
    """
    gift = side.gift
    item, ownership = gift.items[0] if gift.items else (0, Ownership(RENTED, days=1))
    kind = "item" if gift.items else "funds" if gift.funds else "points"
    own = {PERMANENT: "perm", RENTED: "days", COUNTED: "uses"}[ownership.type]
    return SideDraft(
        kind,
        str(gift.points or gift.funds or ""),
        str(item or ""),
        own,
        str(ownership.days or ownership.uses or ""),
        "own" if side.own_text else "imported",
        dict(side.own_text),
    )


def from_form(form: FormData, step: str) -> StepEditor:
    """Return a step's editor as its form's fields write it.

    Args:
        form: The form.
        step: The step's name.
    """
    fields = {name: str(value).strip() for name, value in form.items()}
    sides = {
        side: SideDraft(
            fields.get(f"{side}-kind", ""),
            fields.get(f"{side}-amount", ""),
            fields.get(f"{side}-item", ""),
            fields.get(f"{side}-own", ""),
            fields.get(f"{side}-count", ""),
            fields.get(f"{side}-name", ""),
            {
                language: fields[f"{side}-text-{language}"]
                for language in languages()
                if fields.get(f"{side}-text-{language}")
            },
            fields.get(f"{side}-q", ""),
        )
        for side in SIDES
    }
    return StepEditor(
        step,
        NUMBERS[step],
        fields.get("level", ""),
        fields.get("days", ""),
        sides,
        fields.get("note", ""),
    )


def checked(
    words: RewardTexts, seed: ReferralStep, step: str, written: StepEditor
) -> ReferralStep | dict[str, str]:
    """Return a step as its editor's fields set it, or why each refused field is refused.

    A gift other than the game data's needs own text, as the game's name states the data's.

    Args:
        words: Writes the page's language, with the rewards as the instance sets them.
        seed: The step as the game data sets it.
        step: The step's name.
        written: The editor's fields.
    """
    texts, rules = words.texts, words.rules.referral
    assert rules is not None
    heroes = words.game.heroes
    highest = heroes.highest_level if heroes else MOST_BALANCE
    refused: dict[str, str] = {}
    level = whole_number(written.level, highest)
    first, second = rules.first.level, rules.second.level
    if level is None:
        refused["level"] = texts.text("web.admin.rewards.referral.refused.level", most=highest)
    elif step == "second" and level <= first:
        refused["level"] = texts.text("web.admin.rewards.referral.refused.order", level=first)
    elif step == "first" and level >= second:
        refused["level"] = texts.text("web.admin.rewards.referral.refused.below", level=second)
    days = whole_number(written.days, MOST_DAYS) if step == "second" else None
    if step == "second" and days is None:
        refused["days"] = texts.text("web.admin.rewards.referral.refused.window")
    sides = {}
    for side in SIDES:
        draft, data = written.sides[side], getattr(seed, side)
        gift = _gift(words, draft)
        if isinstance(gift, str):
            refused[f"{side}-gift"] = gift
            continue
        if draft.name == "own" and not draft.texts.get(BASE_LANGUAGE):
            refused[f"{side}-name"] = texts.text("web.admin.rewards.referral.refused.english")
        elif draft.name != "own" and gift != data.gift:
            refused[f"{side}-name"] = texts.text(
                "web.admin.rewards.referral.name.mismatch",
                name=words.catalogue.text(data.name) or data.name,
                stated=", ".join(texts.gift_words(words.catalogue, data.gift)),
                gift=", ".join(texts.gift_words(words.catalogue, gift)),
            )
        texts_of = draft.texts if draft.name == "own" else {}
        sides[side] = ReferralSide(gift=gift, name=data.name, own_text=texts_of)
    if not written.note:
        refused["note"] = texts.text(
            "web.admin.still_needed", what=texts.text("web.admin.still_needed.note")
        )
    if refused or level is None:
        return refused
    return ReferralStep(
        level=level, days=days, recruiter=sides["recruiter"], recruit=sides["recruit"]
    )


def _gift(words: RewardTexts, draft: SideDraft) -> Gift | str:
    # A side's gift as its fields set it, or why it is refused.
    texts = words.texts
    if draft.kind in ("points", "funds"):
        amount = whole_number(draft.amount, MOST_BALANCE)
        if amount is None or (draft.kind == "points" and not words.rules.points):
            return texts.text("web.admin.rewards.referral.refused.empty")
        return Gift(points=amount) if draft.kind == "points" else Gift(funds=amount)
    if draft.kind != "item":
        return texts.text("web.admin.rewards.referral.refused.empty")
    number_of = whole_number(draft.item, MOST_BALANCE)
    item = words.catalogue.item(number_of) if number_of else None
    if number_of is None or (words.catalogue.imported and item is None):
        return texts.text("web.admin.rewards.referral.refused.item", id=draft.item)
    rules = words.game.catalogue
    if rules and number_of in rules.withheld:
        return texts.text("web.admin.keys.refused.withheld", id=number_of)
    # Heroes hold an item as uses where the store sells it so, and only then.
    store = words.game.store
    if item and store and store.counted(item) != (draft.own == "uses"):
        name = words.catalogue.name(number_of) or str(number_of)
        key = "uses" if store.counted(item) else "kept"
        return texts.text(f"web.admin.rewards.referral.refused.{key}", item=name)
    ownership = written_ownership(draft.own, draft.count, MOST_DAYS)
    if ownership is None:
        return texts.text("web.admin.rewards.referral.refused.count")
    return Gift(items=((number_of, ownership),))


def step_written(step: ReferralStep) -> dict[str, Any]:
    """Return a step as the settings and the audit log write it.

    Args:
        step: The step.
    """

    def side(found: ReferralSide) -> dict[str, Any]:
        return {"gift": found.gift.written, "name": found.name, "own_text": found.own_text}

    return {
        "level": step.level,
        "days": step.days,
        "recruiter": side(step.recruiter),
        "recruit": side(step.recruit),
    }
