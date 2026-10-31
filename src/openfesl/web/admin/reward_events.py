import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Role
from openfesl.core.calendar import today
from openfesl.core.catalogues import Item, game_text
from openfesl.core.games import Game
from openfesl.core.reward_claims import RewardStore
from openfesl.core.reward_rules import Gift, Part
from openfesl.core.reward_runs import run_now, run_of
from openfesl.core.texts import localize, translate
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.boxes import BoxAdminViews
from openfesl.web.admin.reward_contents import PartDraft, gift_json
from openfesl.web.admin.reward_editor import Editor, drafted, editor_of, run_days, written
from openfesl.web.admin.rewards import SECTION, EventRow, RewardAdminViews, event_row
from openfesl.web.reward_texts import RewardTexts
from openfesl.web.sessions import check_form, toast

# A line of what a gift gives, with the panel's page of the box it rolls; empty for no box.
type GiftLine = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PartView:
    """A part of an event, for a faction's heroes or the account, as its editor shows it.

    Attributes:
        key: Its key in the event.
        part: The part as the editor's fields set it.
        draft: Its fields as written.
        items: Its items in the order of its fields, each written and in words, with the
            catalogue's item where it has one.
        boxes: The boxes it may roll, each key with its name; empty for the account's own part.
        rolled: The box it rolls in words, with the panel's page of the box; empty for none.
        goal: What one hero must reach first, as labelled facts; empty for no goal.
        texts: The ids of the texts that name and describe it, each with what it says on the
            first day; empty while the game's texts are not imported.
    """

    key: str
    part: Part
    draft: PartDraft
    items: list[tuple[str, str, Item | None]]
    boxes: list[tuple[str, str]]
    rolled: list[GiftLine]
    goal: list[tuple[str, str]]
    texts: list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Candidate:
    """An item that the add panel's search found.

    Attributes:
        item: The item.
        name: Its name.
        part: The key of the first part whose faction it fits; empty when it fits none.
    """

    item: Item
    name: str
    part: str


@dataclass(frozen=True, slots=True)
class EventView:
    """An event of a game's rewards calendar as its page shows it.

    Attributes:
        game: The game.
        words: Writes the page's language.
        row: The event as it runs now or this year.
        first: Its first day of that run, in words.
        last: Its last day of that run, in words.
        starts: When its days start, in words, such as 00:00 UTC.
        parts: Its parts as the editor's fields set them.
        days: The days of a daily event that give other gifts, each with each part's key,
            items as written, box and lines.
        paused: The days of that run on which claims were paused, in words.
        editor: Its editor's fields.
        candidates: The items that the add panel's search found.
        ownerships: The ways the add panel offers to own an item, written such as 7d, each with
            its name.
    """

    game: Game
    words: RewardTexts
    row: EventRow
    first: str
    last: str
    starts: str
    parts: list[PartView]
    days: list[tuple[int, list[tuple[str, tuple[str, ...], str, list[GiftLine]]]]]
    paused: list[str]
    editor: Editor
    candidates: list[Candidate]
    ownerships: list[tuple[str, str]]


class RewardEventViews:
    """Puts together the page of each event of the games' rewards calendars and its editor."""

    def __init__(self, rewards: RewardAdminViews, boxes: BoxAdminViews, store: RewardStore) -> None:
        """Read the events with the boxes they roll and the days on which claims were paused.

        Args:
            rewards: The games' rewards.
            boxes: The stores' boxes, which daily gifts roll.
            store: Where claims and the days on which they were paused are recorded.
        """
        self.rewards = rewards
        self._boxes = boxes
        self._store = store

    async def event(
        self,
        game: Game,
        key: str,
        language: str,
        fields: ImmutableMultiDict[str, Any] | None = None,
    ) -> EventView:
        """Return an event of a game's calendar as it runs now or this year, with its editor.

        Args:
            game: The game, which gives rewards.
            key: The event's key.
            language: The language of the page.
            fields: The editor's fields as written; the event's own when none.

        Raises:
            HTTPException: 404 when the calendar has no event by the key.
        """
        words = await self.rewards.words(game, language)
        texts, rules, zone = words.texts, words.rules, words.rules.zone
        catalogue = words.catalogue
        keys = list(rules.events)
        if key not in keys:
            raise HTTPException(404)
        run = run_now(key, rules.events[key], words.now, zone)
        event = run.event
        _, rows = await self._boxes.rows(game, language)
        boxes = {found.box.key: found for found in rows if found.box.kind == "box"}
        seed = game.rewards.events[key] if game.rewards else event
        draft = drafted(event, run) if fields is None else written(fields, event)
        editor = editor_of(words, seed, run, draft, {key: row.box for key, row in boxes.items()})

        def gifted(gift: Gift) -> list[GiftLine]:
            lines = [(words.item(*item), "") for item in gift.items]
            if gift.box and (box := boxes.get(gift.box)):
                kind = "else" if gift.items else "worth" if box.box.worth_rule else "roll"
                price = texts.priced(box.box.value, box.box.currency)
                said = texts.text(f"web.admin.rewards.gift.box.{kind}", box=box.name, price=price)
                lines.append((said, f"/admin/store/{game.id}/boxes/{box.box.id}"))
            return lines + [(line, "") for line in words.extra(gift)]

        first, last = (today(moment, zone) for moment in (run.period.start, run.period.end))
        edited = editor.contents.parts
        parts = []
        for part_draft in editor.contents.drafts:
            part = edited[part_draft.key]
            facts = []
            if goal := part.goal:
                hero_class = frozenset({goal.hero_class}) if goal.hero_class else None
                facts = [
                    ("goal", texts.amount(goal.count)),
                    ("stat", goal.stat),
                    (
                        "class",
                        texts.classes(hero_class)
                        if hero_class
                        else texts.text("web.admin.rewards.mission.any_class"),
                    ),
                    ("from", words.dated(first)),
                    ("before", words.dated(last)),
                ]
            named = []
            for text in filter(None, (part.name, part.description)):
                # A daily event's texts name their day, which the first day stands in for.
                sample = text.replace("{day}", "1")
                shown = game_text(game, catalogue, sample, language)
                named.append((text, "" if shown == sample else shown))
            items = [
                (written_item, words.item(*item), catalogue.item(item[0]))
                for written_item, item in zip(part_draft.items, part.gift.items, strict=True)
            ]
            offered = [
                (box_key, row.name)
                for box_key, row in boxes.items()
                if part.faction and row.box.faction in (None, part.faction)
            ]
            rolled = [line for line in gifted(part.gift) if line[1]]
            parts.append(
                PartView(part_draft.key, part, part_draft, items, offered, rolled, facts, named)
            )
        numbers = sorted({day for part in edited.values() for day in part.days})
        days = [
            (
                number,
                [
                    (
                        view.key,
                        view.draft.days[number][0] if number in view.draft.days else (),
                        view.draft.days[number][1] if number in view.draft.days else "",
                        gifted(view.part.days[number]) if number in view.part.days else [],
                    )
                    for view in parts
                ],
            )
            for number in numbers
        ]
        paused = await self._store.paused(game.id, first) if event.daily else set()
        rentals = sorted(game.store.rentals) if game.store else []
        ownerships = [
            ("perm", texts.text("web.store.price.permanent")),
            *((f"{days}d", texts.count("web.store.price.days", days)) for days in rentals),
            ("uses", texts.text("web.admin.items.tile.uses")),
        ]
        return EventView(
            game,
            words,
            event_row(words, keys.index(key) + 1, run),
            words.dated(first),
            words.dated(last - timedelta(days=1)),
            words.clock(run.period.start),
            parts,
            days,
            [words.dated(day) for day in sorted(paused) if day < last],
            editor,
            _candidates(game, words, edited, draft.query),
            ownerships,
        )


def _candidates(
    game: Game, words: RewardTexts, parts: dict[str, Part], query: str
) -> list[Candidate]:
    # The items whose name holds the search, or whose number it is, that a reward may give.
    granted = game.heroes.granted if game.heroes else frozenset()
    found = words.catalogue.givable(query, granted)
    return [
        Candidate(
            item,
            name,
            next((key for key, part in parts.items() if part.faction in item.factions), ""),
        )
        for item, name in found
    ]


def reward_event_routes(sessions: AdminSessions, views: RewardEventViews) -> APIRouter:
    """Return the page of each event of a game's rewards calendar, with its editor.

    Every role reads it; changing the event needs the admin role. The add panel finds items and
    adds them to the editor's fields, which Save event saves; nothing is saved while a field is
    refused. A change applies from the next claim and is recorded among the admin's actions.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and change the rewards.
    """
    router = APIRouter()
    path = "/admin/rewards/{game_id}/events/{key}"

    @router.get(path, response_class=HTMLResponse)
    async def event(request: Request, game_id: str, key: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.rewards.game(game_id)
        # The editor's fields as they are typed, which the page answers with what they set.
        query = request.query_params
        found = await views.event(
            game, key, request.state.language, query if "rule" in query else None
        )
        manages = admin.role is Role.ADMIN
        return await sessions.render(
            request, "admin.RewardEvent", admin=admin, found=found, manages=manages
        )

    @router.post(path)
    async def save(request: Request, game_id: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        if form.get("rule") not in ("fixed", "easter", "count"):
            raise HTTPException(400)
        game = views.rewards.game(game_id)
        language = request.state.language
        found = await views.event(game, key, language, form)
        editor = found.editor
        edited = editor.edited
        saving = "find" not in form and "add" not in form
        if not saving or edited is None:
            status = 400 if saving or "add" in editor.refused else 200
            return await sessions.render(
                request,
                "admin.RewardEvent",
                status,
                admin=admin,
                found=found,
                manages=True,
                refusing=saving,
            )
        assert game.rewards is not None
        run, seed = found.row.run, game.rewards.events[key]
        event = run.event
        own = {code: text for code, text in editor.draft.texts.items() if text}
        edited = edited.model_copy(update={"own_text": own})
        title = translate(event.title, language)
        if edited == event:
            toast(request, "web.admin.toast.rewards.event.unchanged", event=title)
            return RedirectResponse(
                localize(f"/admin/rewards/{game.id}/events/{key}", language), 303
            )
        window = edited.window
        settings: dict[str, str | None] = {
            f"events.{key}.window": None
            if window == seed.window
            else json.dumps(window.model_dump(exclude_none=True)),
            f"events.{key}.own_text": json.dumps(own) if own else None,
        }
        changes = []
        for name, part in edited.parts.items():
            before, data = event.parts[name], seed.parts[name]
            prefix = f"events.{key}.parts.{name}"
            on = part.enabled
            settings[f"{prefix}.enabled"] = None if on == data.enabled else "on" if on else "off"
            settings[f"{prefix}.gift"] = (
                None if part.gift == data.gift else json.dumps(gift_json(part.gift))
            )
            days = {str(day): gift_json(gift) for day, gift in part.days.items()}
            settings[f"{prefix}.days"] = None if part.days == data.days else json.dumps(days)
            if on != before.enabled:
                changes.append([name, "on" if on else "off"])
            if (part.gift, part.days) != (before.gift, before.days):
                changes.append([name, "contents"])
        details = {
            "event": key,
            "dates": [run_days(event.window, run.year), run_days(window, run.year)],
            "own_text": [event.own_text, own],
            "parts": changes,
        }
        await views.rewards.change(game, settings, AdminAction.REWARD_EVENT, details, admin)
        dates = found.words.event(run_of(key, edited, run.year, found.words.rules.zone)).dates
        toast(request, "web.admin.toast.rewards.event", event=title, dates=dates)
        return RedirectResponse(localize(f"/admin/rewards/{game.id}/events/{key}", language), 303)

    return router
