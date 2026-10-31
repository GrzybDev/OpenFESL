from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from babel import Locale
from pydantic import ValidationError
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogue, Item
from openfesl.core.gift_codes import CodeTerms, GiftCode
from openfesl.core.inventory import Ownership
from openfesl.core.reward_rules import Gift
from openfesl.core.store_items import requirement
from openfesl.core.texts import BASE_LANGUAGE, languages
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.reward_contents import written_amount, written_ownership
from openfesl.web.pages import listed
from openfesl.web.store_texts import StoreTexts

# The longest rental in days and the longest label.
MOST_DAYS = 365
LABEL_LONGEST = 80
# The ways of giving an item that the editor offers, and the days and uses it starts with.
OWNERSHIPS = ("perm", "days", "uses")
DAYS = "7"
USES = "10"


@dataclass(frozen=True, slots=True)
class CodeDraft:
    """A code's editor as written.

    Attributes:
        code: The code drawn for a new code.
        items: The items it gives, each written such as 1127 7d.
        query: What the item search looks for.
        pick: The number of the item the search chose.
        own: How the chosen item is given: perm, days or uses.
        days: How many days a rental lasts, as written.
        uses: How many uses an item counted in uses brings, as written.
        funds: The Funds it gives the account, as written.
        keys: The treasure chest keys it gives the account, as written.
        limit: any for any number of accounts, limit for at most a number.
        most: How many accounts may redeem it, as written.
        ends_day: The day it ends in UTC, empty for none.
        ends_time: The time of that day it ends in UTC.
        labels: What players see it called, by language code.
        note: Why it is made or changed, for the audit log.
    """

    code: str
    items: tuple[str, ...]
    query: str
    pick: str
    own: str
    days: str
    uses: str
    funds: str
    keys: str
    limit: str
    most: str
    ends_day: str
    ends_time: str
    labels: dict[str, str]
    note: str


@dataclass(frozen=True, slots=True)
class ContentLine:
    """An item of a code's contents, as its editor lists it.

    Attributes:
        item_id: The item's number.
        ownership: How it is given.
        name: Its name.
        item: The catalogue's item; none where the catalogue lacks it.
        whom: The heroes it is for, such as Royal Army; empty for any hero or where unknown.
    """

    item_id: int
    ownership: Ownership
    name: str
    item: Item | None
    whom: str = ""


@dataclass(frozen=True, slots=True)
class OwnTile:
    """One way the editor offers to give the chosen item.

    Attributes:
        key: perm, days or uses.
        glyph: Its glyph.
        hint: What it does, or why the item cannot be given so.
        disabled: Whether the item cannot be given so.
    """

    key: str
    glyph: str
    hint: str
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class CodeEditor:
    """A code's editor: its fields as written and what they come to.

    Attributes:
        entry: The code being changed; none for a new one.
        draft: The fields as written.
        lines: The items it gives.
        candidates: The items the search found, each with its name and the heroes it is for.
        picked: The item the search chose, if any.
        own: How the chosen item would be given: perm, days or uses.
        fit: Which heroes the chosen item fits, or why it cannot be given.
        tiles: The ways to give the chosen item; empty when it cannot be given.
        add: The label of the button that adds the chosen item; empty when it cannot be added.
        summary: What the code gives and on what terms, in a sentence.
        refused: Why a field is refused, by its name.
        terms: What it gives and on what terms; none while a field is refused or for an editor
            that is not saved.
        languages: The languages of its labels, English first, each with its name.
        imported: Whether the game's catalogue is imported, which items need.
        icons: Whether the catalogue holds the items' icons.
    """

    entry: GiftCode | None
    draft: CodeDraft
    lines: list[ContentLine]
    candidates: list[tuple[Item, str, str]]
    picked: Item | None
    own: str
    fit: str
    tiles: list[OwnTile]
    add: str
    summary: str
    refused: dict[str, str]
    terms: CodeTerms | None
    languages: list[tuple[str, str]]
    imported: bool
    icons: bool


def drafted(entry: GiftCode | None, code: str) -> CodeDraft:
    """Return a code's editor fields as it is saved, or those of a new code.

    Args:
        entry: The code; none for a new one.
        code: The code drawn for a new code.
    """
    if entry is None:
        return CodeDraft(code, (), "", "", "", DAYS, USES, "0", "0", "any", "", "", "", {}, "")
    ends = entry.expires
    return CodeDraft(
        entry.code,
        tuple(f"{item_id} {ownership.written}" for item_id, ownership in entry.gift.items),
        "",
        "",
        "",
        DAYS,
        USES,
        str(entry.gift.funds),
        str(entry.gift.keys),
        "limit" if entry.max_uses is not None else "any",
        str(entry.max_uses or ""),
        f"{ends:%Y-%m-%d}" if ends else "",
        f"{ends:%H:%M}" if ends else "",
        dict(entry.labels),
        "",
    )


def written(fields: ImmutableMultiDict[str, Any]) -> CodeDraft:
    """Return a code's editor fields as a form wrote them, less the item its drop button names.

    Args:
        fields: The form's fields.
    """

    def field(name: str) -> str:
        return str(fields.get(name, "")).strip()

    items = [str(item) for item in fields.getlist("item")]
    dropped = field("drop")
    if dropped.isascii() and dropped.isdecimal() and int(dropped) < len(items):
        del items[int(dropped)]
    return CodeDraft(
        field("code"),
        tuple(items),
        field("q"),
        field("pick"),
        field("own"),
        # The fields of a way of giving an item come with the item's choice, until then the
        # defaults.
        str(fields.get("days", DAYS)).strip(),
        str(fields.get("uses", USES)).strip(),
        field("funds") or "0",
        field("keys") or "0",
        field("limit") or "any",
        field("most"),
        field("ends_day"),
        field("ends_time"),
        {code: field(f"label-{code}")[:LABEL_LONGEST] for code in languages()},
        field("note"),
    )


def editor_of(
    texts: StoreTexts,
    catalogue: Catalogue,
    entry: GiftCode | None,
    draft: CodeDraft,
    adding: bool = False,
    saving: bool = False,
) -> CodeEditor:
    """Return a code's editor for its fields as written, adding the chosen item or checking all.

    Args:
        texts: Writes the page's language for the code's game.
        catalogue: The game's imported catalogue in the page's language.
        entry: The code being changed; none for a new one.
        draft: The fields as written.
        adding: Whether the add button sent the form, which adds the chosen item.
        saving: Whether the form is saved, which checks every field.

    Raises:
        HTTPException: 400 for fields that no form of the editor sends.
    """
    if draft.limit not in ("any", "limit"):
        raise HTTPException(400)
    refused: dict[str, str] = {}
    lines = _lines(texts, catalogue, draft.items)
    picked = _item(catalogue, draft.pick)
    if adding:
        if picked is None or picked.withheld:
            raise HTTPException(400)
        draft, lines = _added(texts, catalogue, draft, lines, picked, refused)
        picked = _item(catalogue, draft.pick)
    own, fit, tiles, add = _choice(texts, catalogue, picked, draft)
    amounts = [written_amount(draft.funds), written_amount(draft.keys)]
    if None in amounts:
        refused["amounts"] = texts.text(
            "web.admin.rewards.refused.amount", most=texts.amount(MOST_BALANCE)
        )
    try:
        gift = Gift.model_validate(
            {"items": draft.items, "funds": amounts[0] or 0, "keys": amounts[1] or 0}
        )
    except ValidationError:
        raise HTTPException(400) from None
    if conflict := _conflict(texts, lines):
        refused["items"] = conflict
    ends = _ends(draft)
    terms = None
    if saving:
        _check(texts, draft, gift, ends, refused)
        if not refused:
            most = int(draft.most) if draft.limit == "limit" else None
            labels = {code: label for code, label in draft.labels.items() if label}
            terms = CodeTerms(gift, labels, most, ends if isinstance(ends, datetime) else None)
    locale = Locale.parse(texts.language, sep="-")
    heroes = texts.game.heroes
    # Items the game grants its heroes itself, such as missions, are not given.
    granted = heroes.granted if heroes else frozenset()
    found = catalogue.search(draft.query, lambda item: item.kind not in granted)
    return CodeEditor(
        entry,
        draft,
        lines,
        [(item, name, _whom(texts, item)) for item, name in found],
        picked,
        own,
        fit,
        tiles,
        add,
        _summary(texts, catalogue, draft, gift, ends),
        refused,
        terms,
        [(code, str(locale.languages.get(code, code))) for code in languages()],
        catalogue.imported,
        catalogue.contents.icons > 0,
    )


def _lines(texts: StoreTexts, catalogue: Catalogue, items: tuple[str, ...]) -> list[ContentLine]:
    # The contents as the form keeps them; a withheld item is given by hand alone.
    found = []
    for text in items:
        number, _, own = text.partition(" ")
        try:
            ownership = Ownership.read(own)
        except ValueError:
            raise HTTPException(400) from None
        if not (number.isascii() and number.isdecimal()) or not isinstance(ownership, Ownership):
            raise HTTPException(400)
        item = catalogue.item(int(number))
        if item and item.withheld:
            raise HTTPException(400)
        name = catalogue.name(int(number)) or number
        whom = _whom(texts, item) if item else ""
        found.append(ContentLine(int(number), ownership, name, item, whom))
    return found


def _item(catalogue: Catalogue, number: str) -> Item | None:
    return catalogue.item(int(number)) if number.isascii() and number.isdecimal() else None


def _counted(texts: StoreTexts, item: Item) -> bool:
    store = texts.game.store
    return store is not None and store.counted(item)


def _choice(
    texts: StoreTexts, catalogue: Catalogue, picked: Item | None, draft: CodeDraft
) -> tuple[str, str, list[OwnTile], str]:
    # How the chosen item would be given, which heroes it fits, the ways to give it and the add
    # button's label; a withheld item is refused at once.
    if picked is None:
        return "", "", [], ""
    if picked.withheld:
        return "", texts.text("web.admin.keys.refused.withheld", id=picked.id), [], ""
    name = catalogue.name(picked.id) or str(picked.id)
    counted = _counted(texts, picked)
    rule = texts.requires(requirement(texts.game, picked))
    fit = texts.text("web.admin.keys.fits", item=name, rule=rule)
    held = texts.text("web.admin.items.tile.counted")
    tiles = [
        OwnTile("perm", "infinity", held if counted else texts.text("web.admin.keys.own.perm")),
        OwnTile("days", "clock", held if counted else texts.text("web.admin.keys.own.days")),
        OwnTile(
            "uses",
            "uses",
            texts.text(
                "web.admin.keys.own.uses" if counted else "web.admin.items.tile.not_counted"
            ),
            not counted,
        ),
    ]
    tiles = [replace(tile, disabled=counted) if tile.key != "uses" else tile for tile in tiles]
    own = draft.own if draft.own in OWNERSHIPS else ""
    if not own or (own == "uses") != counted:
        own = "uses" if counted else "days"
    gained = written_ownership(own, draft.days if own == "days" else draft.uses, MOST_DAYS)
    what = texts.ownership(gained, inline=True) if gained else ""
    add = texts.text("web.admin.keys.add_item", item=name, what=what) if gained else ""
    return own, fit, tiles, add


def _added(
    texts: StoreTexts,
    catalogue: Catalogue,
    draft: CodeDraft,
    lines: list[ContentLine],
    picked: Item,
    refused: dict[str, str],
) -> tuple[CodeDraft, list[ContentLine]]:
    # The contents with the chosen item added, unless it cannot be given so or no hero could
    # take it with the others.
    name = catalogue.name(picked.id) or str(picked.id)
    counted = _counted(texts, picked)
    if draft.own not in OWNERSHIPS:
        raise HTTPException(400)
    if (draft.own == "uses") != counted:
        key = "counted" if counted else "not_counted"
        refused["add"] = texts.text(f"web.admin.keys.refused.{key}", item=name)
        return draft, lines
    days, uses = draft.days, draft.uses
    gained = written_ownership(draft.own, days if draft.own == "days" else uses, MOST_DAYS)
    if gained is None:
        refused["add"] = (
            texts.text("web.admin.keys.refused.days", most=texts.amount(MOST_DAYS))
            if draft.own == "days"
            else texts.text("web.admin.rewards.refused.uses", most=texts.amount(MOST_BALANCE))
        )
        return draft, lines
    line = ContentLine(picked.id, gained, name, picked, _whom(texts, picked))
    if conflict := _conflict(texts, [*lines, line]):
        refused["add"] = conflict
        return draft, lines
    written = f"{picked.id} {gained.written}"
    return replace(draft, items=(*draft.items, written), pick=""), [*lines, line]


def _conflict(texts: StoreTexts, lines: list[ContentLine]) -> str:
    # Why no single hero could take every item, naming two that no hero takes together; empty
    # when one could. An item the catalogue lacks is given by its number, unchecked.
    known = [line for line in lines if line.item]
    for index, first in enumerate(known):
        for second in known[index + 1 :]:
            assert first.item is not None and second.item is not None
            factions = first.item.factions & second.item.factions
            if factions and first.item.classes & second.item.classes:
                continue
            return texts.text(
                "web.admin.keys.refused.no_single_hero",
                a=first.name,
                fa=_whom(texts, first.item),
                b=second.name,
                fb=_whom(texts, second.item),
            )
    return ""


def _whom(texts: StoreTexts, item: Item) -> str:
    # The heroes an item is for, by faction and class, such as Royal Army Commando.
    found = requirement(texts.game, item)
    parts = [texts.factions(found.factions)] if found.factions else []
    parts += [texts.classes(found.classes)] if found.classes else []
    return " ".join(parts)


def _ends(draft: CodeDraft) -> datetime | bool | None:
    # When it ends in UTC; None for no end and False for a day or time that is no moment.
    if not draft.ends_day:
        return None
    try:
        moment = datetime.fromisoformat(f"{draft.ends_day}T{draft.ends_time or '00:00'}")
    except ValueError:
        return False
    return moment.replace(tzinfo=UTC)


def _summary(
    texts: StoreTexts,
    catalogue: Catalogue,
    draft: CodeDraft,
    gift: Gift,
    ends: datetime | bool | None,
) -> str:
    # What the code gives and on what terms, in a sentence.
    if draft.limit == "limit" and draft.most.isdecimal() and int(draft.most):
        whom = texts.count("web.admin.keys.summary.most", int(draft.most))
    else:
        whom = texts.text("web.admin.keys.summary.any")
    until = (
        texts.text("web.admin.keys.summary.until", end=texts.moment(ends))
        if isinstance(ends, datetime)
        else texts.text("web.admin.keys.summary.no_end")
    )
    words = texts.gift_words(catalogue, gift)
    if not words:
        return texts.text("web.admin.keys.summary.nothing", whom=whom, until=until)
    return texts.text(
        "web.admin.keys.summary", whom=whom, until=until, gift=listed(words, texts.language)
    )


def _check(
    texts: StoreTexts,
    draft: CodeDraft,
    gift: Gift,
    ends: datetime | bool | None,
    refused: dict[str, str],
) -> None:
    # The refusals of a code as it is saved, beside the fields they concern.
    if not (gift.items or gift.funds or gift.keys) and "amounts" not in refused:
        refused["items"] = texts.text("web.admin.keys.refused.empty")
    if draft.limit == "limit":
        most = draft.most
        if not (most.isascii() and most.isdecimal() and 0 < int(most) <= MOST_BALANCE):
            refused["most"] = texts.text("web.admin.key.limits.wrong")
    if ends is False:
        refused["ends"] = texts.text("web.admin.keys.refused.when")
    elif isinstance(ends, datetime) and ends <= datetime.now(UTC):
        refused["ends"] = texts.text("web.admin.keys.refused.past", end=texts.moment(ends))
    if not draft.labels.get(BASE_LANGUAGE):
        refused["label"] = texts.text("web.admin.keys.refused.label")
    if not draft.note:
        refused["note"] = texts.text("web.admin.keys.refused.note")
