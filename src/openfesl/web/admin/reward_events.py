from dataclasses import dataclass
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException

from openfesl.core.calendar import today
from openfesl.core.catalogues import game_text
from openfesl.core.games import Game
from openfesl.core.reward_claims import RewardStore
from openfesl.core.reward_rules import Gift, Part
from openfesl.core.reward_runs import run_of
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.boxes import BoxAdminViews
from openfesl.web.admin.rewards import EventRow, RewardAdminViews, event_row
from openfesl.web.reward_texts import RewardTexts

# A line of what a gift gives, with the panel's page of the box it rolls; empty for no box.
type GiftLine = tuple[str, str]


@dataclass(frozen=True, slots=True)
class PartView:
    """A part of an event, for a faction's heroes or the account, as its page shows it.

    Attributes:
        key: Its key in the event.
        part: The part as the instance sets it.
        gift: What it gives, a line each.
        goal: What one hero must reach first, as labelled facts; empty for no goal.
        texts: The ids of the texts that name and describe it, each with what it says on the
            first day; empty while the game's texts are not imported.
    """

    key: str
    part: Part
    gift: list[GiftLine]
    goal: list[tuple[str, str]]
    texts: list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class EventView:
    """An event of a game's rewards calendar as its page shows it.

    Attributes:
        game: The game.
        words: Writes the page's language.
        row: The event in this year.
        first: Its first day this year, in words.
        last: Its last day this year, in words.
        starts: When its days start, in words, such as 00:00 UTC.
        parts: Its parts.
        days: The days of a daily event that give other gifts, each with each part's.
        paused: The days of this year's run on which claims were paused, in words.
    """

    game: Game
    words: RewardTexts
    row: EventRow
    first: str
    last: str
    starts: str
    parts: list[PartView]
    days: list[tuple[int, list[list[GiftLine]]]]
    paused: list[str]


class RewardEventViews:
    """Puts together the page of each event of the games' rewards calendars."""

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

    async def event(self, game: Game, key: str, language: str) -> EventView:
        """Return an event of a game's calendar in this year.

        Args:
            game: The game, which gives rewards.
            key: The event's key.
            language: The language of the page.

        Raises:
            HTTPException: 404 when the calendar has no event by the key.
        """
        words = await self.rewards.words(game, language)
        texts, rules, zone = words.texts, words.rules, words.rules.zone
        keys = list(rules.events)
        if key not in keys:
            raise HTTPException(404)
        run = run_of(key, rules.events[key], today(words.now, zone).year, zone)
        row = event_row(words, keys.index(key) + 1, run)
        _, rows = await self._boxes.rows(game, language)
        boxes = {found.box.key: found for found in rows}

        def gifted(gift: Gift) -> list[GiftLine]:
            lines = [(words.item(*item), "") for item in gift.items]
            if gift.box and (box := boxes.get(gift.box)):
                kind = "else" if gift.items else "worth" if box.box.worth_rule else "roll"
                price = texts.priced(box.box.value, box.box.currency)
                said = texts.text(f"web.admin.rewards.gift.box.{kind}", box=box.name, price=price)
                lines.append((said, f"/admin/store/{game.id}/boxes/{box.box.id}"))
            return lines + [(line, "") for line in words.extra(gift)]

        first, last = (today(moment, zone) for moment in (run.period.start, run.period.end))
        event = run.event
        parts = []
        for name, part in event.parts.items():
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
                shown = game_text(game, words.catalogue, sample, language)
                named.append((text, "" if shown == sample else shown))
            parts.append(PartView(name, part, gifted(part.gift), facts, named))
        numbers = sorted({day for part in event.parts.values() for day in part.days})
        days = [
            (number, [gifted(part.on(number)) for part in event.parts.values()])
            for number in numbers
        ]
        paused = await self._store.paused(game.id, first) if event.daily else set()
        return EventView(
            game,
            words,
            row,
            words.dated(first),
            words.dated(last - timedelta(days=1)),
            words.clock(run.period.start),
            parts,
            days,
            [words.dated(day) for day in sorted(paused) if day < last],
        )


def reward_event_routes(sessions: AdminSessions, views: RewardEventViews) -> APIRouter:
    """Return the page of each event of a game's rewards calendar, which every role reads.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show.
    """
    router = APIRouter()

    @router.get("/admin/rewards/{game_id}/events/{key}", response_class=HTMLResponse)
    async def event(request: Request, game_id: str, key: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.rewards.game(game_id)
        found = await views.event(game, key, request.state.language)
        return await sessions.render(request, "admin.RewardEvent", admin=admin, found=found)

    return router
