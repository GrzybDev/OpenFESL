from dataclasses import dataclass
from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from openfesl.core.admins import Role
from openfesl.core.calendar import day_start, today
from openfesl.core.games import Game
from openfesl.core.reward_runs import run_of, running_at
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.rewards import RewardAdminViews

# How many of the next events' first days the preview offers to jump to, and how many years
# away from today a date may be.
NEXT_DATES = 3
MOST_YEARS = 100


@dataclass(frozen=True, slots=True)
class PreviewCard:
    """A reward as the players' rewards page shows it on a date.

    Attributes:
        number: The number of its event in the calendar from 1; none for a reward that no event
            brings, which the page shows whenever it applies.
        title: Its name.
        text: What it gives, in a sentence.
        state: today for a daily gift, claimable, locked for a goal, or always.
        said: Its state in words.
    """

    number: int | None
    title: str
    text: str
    state: str
    said: str


@dataclass(frozen=True, slots=True)
class Preview:
    """The players' rewards page of a game on a date, as an eligible account sees it.

    Attributes:
        game: The game.
        day: The date.
        shown: The date in words.
        today: Today in the rewards' zone.
        jumps: The first days of the next events after the date, each with its words.
        events: How many of the cards come from the calendar.
        cards: The rewards, those of the calendar first.
        paused: Whether claims are paused now, which the players' page says instead.
    """

    game: Game
    day: date
    shown: str
    today: date
    jumps: list[tuple[date, str]]
    events: int
    cards: list[PreviewCard]
    paused: bool


def reward_preview_routes(sessions: AdminSessions, views: RewardAdminViews) -> APIRouter:
    """Return the preview of each game's rewards page on a date, which every role reads.

    It shows the page as an account with heroes of both factions sees it on that date; nothing is
    claimed or written. A date that is none shows today.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: The games' rewards.
    """
    router = APIRouter()

    @router.get("/admin/rewards/{game_id}/preview", response_class=HTMLResponse)
    async def show(
        request: Request, game_id: str, chosen: str = Query("", alias="date")
    ) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        language = request.state.language
        words = await views.words(game, language)
        texts, rules, zone = words.texts, words.rules, words.rules.zone
        now = today(words.now, zone)
        try:
            day = date.fromisoformat(chosen)
        except ValueError:
            day = now
        if abs(day.year - now.year) > MOST_YEARS:
            day = now
        keys = list(rules.events)
        cards = []
        for run in running_at(rules, day_start(day, zone)):
            if not any(part.enabled for part in run.event.parts.values()):
                continue
            if any(part.goal for part in run.event.parts.values()):
                state, said = "locked", texts.text("web.admin.rewards.preview.locked")
            elif run.event.daily:
                state = "today"
                said = texts.text("web.admin.rewards.preview.today", day=run.day, days=run.days)
            else:
                state, said = "claimable", texts.text("web.admin.rewards.preview.claimable")
            title, summary = texts.text(run.event.title), texts.text(run.event.summary)
            cards.append(PreviewCard(keys.index(run.key) + 1, title, summary, state, said))
        events = len(cards)
        always = texts.text("web.admin.rewards.preview.applies")
        if (packs := rules.starter) and packs.enabled:
            text = texts.count("web.admin.rewards.preview.packs", packs.days)
            cards.append(PreviewCard(None, texts.text(packs.name), text, "always", always))
        if switched := [found for found in rules.milestones.values() if found.enabled]:
            text = texts.count("web.admin.rewards.preview.milestones", len(switched))
            met = texts.text("web.admin.rewards.preview.met")
            cards.append(
                PreviewCard(None, texts.text("web.admin.rewards.milestones"), text, "always", met)
            )
        if (chest := rules.first_chest) and chest.enabled and game.chests:
            title = texts.text("web.admin.rewards.keys.first")
            text = texts.text("web.admin.rewards.preview.key")
            cards.append(PreviewCard(None, title, text, "always", always))
        starts = sorted(
            first
            for key, event in rules.events.items()
            if event.enabled
            for year in (day.year, day.year + 1)
            if (first := today(run_of(key, event, year, zone).period.start, zone)) > day
        )
        jumps = [(first, words.dated(first)) for first in starts[:NEXT_DATES]]
        shown_day = words.day(day_start(day, zone), "yMMMd")
        found = Preview(game, day, shown_day, now, jumps, events, cards, rules.paused)
        return await sessions.render(
            request,
            "admin.RewardPreview",
            admin=admin,
            found=found,
            pause=await views.pause(game),
            manages=admin.role is Role.ADMIN,
        )

    return router
