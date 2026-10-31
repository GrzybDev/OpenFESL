from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from babel import Locale
from babel.dates import format_interval

from openfesl.core.calendar import Window, today
from openfesl.core.games import Game
from openfesl.core.reward_rules import Event
from openfesl.core.reward_runs import Run, run_of
from openfesl.core.texts import languages, localize, translate
from openfesl.web.reward_texts import RewardTexts

# The most characters of an own text, which a reward's card shows in full.
LONGEST_TEXT = 300
# The most days a run lies from Easter Sunday, so that it falls in the year before, of or after
# its Easter, where the calendar looks for the run of a day.
AROUND_EASTER = 365
# The editor's fields of the days, in the order the page shows them.
DATE_FIELDS = ("first", "last", "count", "before", "after")


@dataclass(frozen=True, slots=True)
class Draft:
    """The fields of an event's editor as written: its date rule, days and own text.

    Attributes:
        rule: fixed, easter or count.
        first: The first day of a fixed or counted run, as a date of the run's year.
        last: The last day of a fixed run, likewise.
        count: How many days a counted run lasts.
        before: The first day of a run around Easter, in days from Easter Sunday.
        after: The last day of such a run, likewise.
        texts: The own text by language.
    """

    rule: str
    first: str
    last: str
    count: str
    before: str
    after: str
    texts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Editor:
    """What an event's editor shows of its fields: refusals, its days and the text rule.

    Attributes:
        draft: The fields as written.
        window: The days they set; none while one is refused.
        refused: Why a field is refused, by its name.
        line: What the days set, in words, or why they are refused.
        needs: Why the event needs own text, a sentence each; empty while its texts still
            state it truly.
        languages: The languages of the own text, English first, each with its name.
    """

    draft: Draft
    window: Window | None
    refused: dict[str, str]
    line: str
    needs: list[str]
    languages: list[tuple[str, str]]

    @property
    def longest(self) -> int:
        """The most characters of an own text."""
        return LONGEST_TEXT

    @property
    def blocked(self) -> str:
        """Why the days cannot be saved; empty when they can."""
        return next((self.refused[name] for name in DATE_FIELDS if name in self.refused), "")


def drafted(event: Event, run: Run) -> Draft:
    """Return the editor's fields of an event as the instance sets it.

    A date rule that the event does not use starts from the run's days, or from Easter Sunday.

    Args:
        event: The event.
        run: Its run that the editor shows.
    """
    window = event.window
    first, last = window.days(run.year)
    rule = "easter" if window.easter else "count" if window.count else "fixed"
    before, after = window.easter or (0, 0)
    texts = dict(event.own_text)
    return Draft(rule, str(first), str(last), str(run.days), str(before), str(after), texts)


def written(fields: Mapping[str, str], event: Event) -> Draft:
    """Return the editor's fields as a form wrote them.

    Args:
        fields: The form's fields.
        event: The event, whose own text stays as it is where the form does not show it.
    """
    names = [f"text-{code}" for code in languages()]
    texts = {name.removeprefix("text-"): fields.get(name, "").strip() for name in names}
    return Draft(
        fields.get("rule", ""),
        fields.get("first", "").strip(),
        fields.get("last", "").strip(),
        fields.get("count", "").strip(),
        fields.get("before", "").strip(),
        fields.get("after", "").strip(),
        texts if any(name in fields for name in names) else dict(event.own_text),
    )


def editor_of(words: RewardTexts, seed: Event, run: Run, draft: Draft) -> Editor:
    """Return an event's editor: what its fields set, which is refused and why own text is due.

    Args:
        words: Writes the page's language.
        seed: The event as the game's data sets it, whose texts state what they state.
        run: The event's run that the editor shows, as the instance sets it.
        draft: The editor's fields.
    """
    texts, zone = words.texts, words.rules.zone
    refused: dict[str, str] = {}
    window = _window(draft, refused, texts.text)
    if window and window.overlaps:
        first, last = window.days(run.year)
        refused["count" if draft.rule == "count" else "after"] = texts.text(
            "web.admin.rewards.refused.overlap",
            event=texts.text(run.event.title),
            days=texts.amount((last - first).days + 1),
        )
        window = None
    line = next(iter(refused.values()), "")
    needs = []
    if window:
        edited = run.event.model_copy(update={"window": window})
        shown = run_of(run.key, edited, run.year, zone)
        first, last = today(shown.period.start, zone), today(shown.period.end, zone)
        dates = {"first": words.dated(first), "last": words.dated(last - timedelta(1))}
        if edited.daily:
            line = texts.count("web.admin.rewards.event.daily", shown.days, **dates)
        elif any(part.goal for part in edited.parts.values()):
            line = texts.text("web.admin.rewards.event.goals", **dates)
        else:
            line = texts.text("web.admin.rewards.event.once", **dates)
        data_first, data_last = seed.window.days(run.year)
        needs = [
            texts.text(
                f"web.admin.rewards.text_rule.{reason}",
                days=texts.amount((data_last - data_first).days + 1),
                day=texts.amount(shown.days),
                date=words.dated(data_last),
                new=dates["last"],
            )
            for reason in seed.outdated(edited, run.year)
        ]
    if (needs or any(draft.texts.values())) and not draft.texts.get("en"):
        refused["text-en"] = texts.text("web.admin.rewards.refused.own")
    if any(len(text) > LONGEST_TEXT for text in draft.texts.values()):
        most = texts.amount(LONGEST_TEXT)
        refused["text-en"] = texts.text("web.admin.rewards.refused.long", most=most)
    named = [(code, str(words.locale.languages.get(code, code))) for code in languages()]
    return Editor(draft, window, refused, line, needs, named)


def _window(draft: Draft, refused: dict[str, str], say: Callable[..., str]) -> Window | None:
    # The days that the fields set, or none with the field that refuses them.
    if draft.rule == "easter":
        before, after = _number(draft.before), _number(draft.after)
        if before is None or after is None:
            refused["before" if before is None else "after"] = say(
                "web.admin.rewards.refused.easter", most=AROUND_EASTER
            )
            return None
        if before > after:
            refused["after"] = say("web.admin.rewards.refused.order")
            return None
        if before < -AROUND_EASTER or after > AROUND_EASTER:
            refused["before" if before < -AROUND_EASTER else "after"] = say(
                "web.admin.rewards.refused.easter", most=AROUND_EASTER
            )
            return None
        return Window(easter=(before, after))
    first = _month_day(draft.first, "first", refused, say)
    if draft.rule == "count":
        count = _number(draft.count)
        if count is None or count < 1:
            refused["count"] = say("web.admin.rewards.refused.count")
            return None
        return Window(first=first, count=count) if first else None
    last = _month_day(draft.last, "last", refused, say)
    return Window(dates=(first, last)) if first and last else None


def _month_day(
    text: str, name: str, refused: dict[str, str], say: Callable[..., str]
) -> str | None:
    # A day of every year from a date field's value, such as 12-01 from 2026-12-01.
    try:
        day = date.fromisoformat(text)
    except ValueError:
        refused[name] = say("web.admin.rewards.refused.day")
        return None
    if (day.month, day.day) == (2, 29):
        refused[name] = say("web.admin.rewards.refused.leap")
        return None
    return f"{day.month:02}-{day.day:02}"


def _number(text: str) -> int | None:
    # A whole number of days as the form writes it, below 0 before Easter; any other text counts
    # as none.
    digits = text.removeprefix("-")
    return int(text) if digits.isascii() and digits.isdecimal() and len(digits) < 4 else None


def run_days(window: Window, year: int) -> list[str]:
    """Return the first and last days of a window's run of a year, as the admin's log keeps them.

    Args:
        window: The window.
        year: The run's year.
    """
    return [day.isoformat() for day in window.days(year)]


def event_entry(game: Game, details: dict[str, Any], language: str) -> tuple[str, str, str]:
    """Return how the audit log shows a change of an event's days or own text.

    Args:
        game: The game, whose rewards hold the event.
        details: The change's details, as the admin's log keeps them.
        language: The log's language.

    Returns:
        What the change was done to in words, the panel's page of it and what changed in words.
    """
    assert game.rewards is not None
    key = details["event"]
    event = game.rewards.events.get(key)
    shown = translate(event.title, language) if event else key
    locale = Locale.parse(translate("web.locale", language))
    said = []
    before, after = details["dates"]
    if before != after:
        written = [
            str(
                format_interval(
                    date.fromisoformat(first), date.fromisoformat(last), "MMMd", locale=locale
                )
            )
            for first, last in (before, after)
        ]
        values = {"before": written[0], "after": written[1]}
        said.append(translate("web.admin.audit.reward_dates", language) % values)
    own_before, own_after = details["own_text"]
    if own_after and not own_before:
        named = ", ".join(str(locale.languages.get(code, code)) for code in own_after)
        said.append(translate("web.admin.audit.reward_text", language) % {"languages": named})
    elif own_before and not own_after:
        said.append(translate("web.admin.audit.reward_text.removed", language))
    elif own_before != own_after:
        said.append(translate("web.admin.audit.reward_text.changed", language))
    href = localize(f"/admin/rewards/{game.id}/events/{key}", language)
    return shown, href, "; ".join(said)
