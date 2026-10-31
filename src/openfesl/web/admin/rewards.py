from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.calendar import day_start, today
from openfesl.core.catalogues import Catalogues, game_text
from openfesl.core.games import Game, Games
from openfesl.core.reward_rules import Milestone, Part, RewardRules, StarterPacks
from openfesl.core.reward_runs import Run, run_of
from openfesl.core.rewards import Rewards
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.reward_texts import RewardTexts
from openfesl.web.store_texts import StoreTexts

SECTION = "rewards"
# The calendar strip's scale: its width for each day of the year, and the narrowest event, which
# still holds its number.
DAY = 4
NARROWEST = 36


@dataclass(frozen=True, slots=True)
class EventRow:
    """An event of a game's rewards calendar in this year, as the panel lists it.

    Attributes:
        key: Its key in the game's data.
        number: Its place in the calendar from 1, which the panel shows as E1.
        run: When it runs this year.
        state: off, ended, running, paused or next.
        said: Its state in words, such as Running · day 10 of 13.
        dates: Its days this year in words, such as 19 to 31 Oct.
        rule: Which rule sets its days, in words.
        own: Whether OpenFESL's own texts name its parts rather than the game's.
    """

    key: str
    number: int
    run: Run
    state: str
    said: str
    dates: str
    rule: str
    own: bool


@dataclass(frozen=True, slots=True)
class Chip:
    """An event as the calendar strip draws it.

    Attributes:
        row: The event.
        x: Where its first day starts on the strip.
        width: How wide it is.
        line: Which of the strip's lines it sits on, from 0, so that events never overlap.
    """

    row: EventRow
    x: int
    width: int
    line: int


@dataclass(frozen=True, slots=True)
class Strip:
    """The calendar strip of a year, from January to December.

    Attributes:
        width: Its width.
        lines: How many lines of events it has.
        months: Where each month starts on it, with its short name.
        chips: The events.
        today: Where today is on it.
    """

    width: int
    lines: int
    months: list[tuple[int, str]]
    chips: list[Chip]
    today: int


@dataclass(frozen=True, slots=True)
class Mission:
    """A part of an event that one hero must reach a goal for, as the panel lists it.

    Attributes:
        event: The event.
        part: The part, which has a goal.
        name: Its name.
        line: Its goal, whose heroes count, until when and what it gives, in words.
    """

    event: EventRow
    part: Part
    name: str
    line: str


@dataclass(frozen=True, slots=True)
class Packs:
    """The starter packs as the panel lists them.

    Attributes:
        rules: The packs as the instance sets them.
        classes: The classes in the order of the columns.
        rows: Each faction with what its pack holds for each class besides the shared items.
        shared: The items in every pack, in words.
    """

    rules: StarterPacks
    classes: list[str]
    rows: list[tuple[str, list[str]]]
    shared: str


@dataclass(frozen=True, slots=True)
class RewardsView:
    """A game's rewards as the panel's page shows them.

    Attributes:
        game: The game.
        rules: Its rewards as the instance sets them.
        words: Writes the page's language.
        year: This year in the rewards' zone.
        today: Today in the rewards' zone, in words.
        starts: When the rewards' days start, in words, such as 00:00 UTC.
        events: The calendar's events in this year.
        strip: The calendar strip.
        missions: The parts of events that one hero must reach a goal for.
        packs: The starter packs; none for a game without them.
        milestones: The milestones by key, each with its name, which states its goal.
        chest: The name of the reward of a first treasure chest; empty for a game without one.
    """

    game: Game
    rules: RewardRules
    words: RewardTexts
    year: int
    today: str
    starts: str
    events: list[EventRow]
    strip: Strip
    missions: list[Mission]
    packs: Packs | None
    milestones: list[tuple[str, Milestone, str]]
    chest: str


class RewardAdminViews:
    """Puts together what the admin panel shows of the games' rewards."""

    def __init__(self, games: Games, rewards: Rewards, catalogues: Catalogues) -> None:
        """Read the rewards of the games that give them.

        Args:
            games: The games, of which those with reward rules give rewards.
            rewards: The rewards, with the instance's settings over the games' data.
            catalogues: The games' imported catalogues, which name the items.
        """
        self.games = [game for game in games if game.rewards]
        self.rewards = rewards
        self._catalogues = catalogues

    def game(self, game_id: str) -> Game:
        """Return a game that gives rewards by its id.

        Args:
            game_id: The game's id.

        Raises:
            HTTPException: 404 when no game with rewards has the id.
        """
        game = next((game for game in self.games if game.id == game_id), None)
        if game is None:
            raise HTTPException(404)
        return game

    async def words(self, game: Game, language: str) -> RewardTexts:
        """Return what the panel says of a game's rewards, in a language.

        Args:
            game: The game, which gives rewards.
            language: The language.
        """
        rules = await self.rewards.rules(game)
        assert rules is not None
        texts = StoreTexts(game, language, datetime.now(UTC))
        catalogue = await self._catalogues.of(game, language)
        return RewardTexts(game, rules, texts, catalogue, None, False)

    async def view(self, game: Game, language: str) -> RewardsView:
        """Return a game's rewards with this year's calendar.

        Args:
            game: The game, which gives rewards.
            language: The language of the page.
        """
        words = await self.words(game, language)
        texts, rules, zone = words.texts, words.rules, words.rules.zone
        year = today(words.now, zone).year
        events = [
            event_row(words, number, run_of(key, event, year, zone))
            for number, (key, event) in enumerate(rules.events.items(), 1)
        ]
        missions = []
        for row in events:
            for part in row.run.event.parts.values():
                if goal := part.goal:
                    hero_class = frozenset({goal.hero_class}) if goal.hero_class else None
                    given = [words.item(*item) for item in part.gift.items]
                    line = texts.text(
                        "web.admin.rewards.mission.line",
                        goal=texts.amount(goal.count),
                        stat=goal.stat,
                        faction=texts.factions(frozenset({part.faction})) if part.faction else "",
                        hero_class=texts.classes(hero_class)
                        if hero_class
                        else texts.text("web.admin.rewards.mission.any_class"),
                        date=words.dated(today(row.run.period.end, zone)),
                        gift=", ".join(given + words.extra(part.gift)),
                    )
                    name = game_text(game, words.catalogue, part.name, language)
                    if name == part.name:
                        # Before the game's texts are imported, the event's own name stands in.
                        name = texts.text(row.run.event.title)
                    missions.append(Mission(row, part, name, line))
        packs = None
        if starter := rules.starter:
            # The items that every pack holds, then what each one holds besides them.
            gifts = [gift for found in starter.packs.values() for gift in found.values()]
            shared = [item for item in gifts[0].items if all(item in g.items for g in gifts)]
            classes = list(
                dict.fromkeys(name for found in starter.packs.values() for name in found)
            )
            rows = [
                (
                    faction,
                    [
                        ", ".join(
                            words.item(*item) for item in found[name].items if item not in shared
                        )
                        if name in found
                        else ""
                        for name in classes
                    ],
                )
                for faction, found in starter.packs.items()
            ]
            packs = Packs(starter, classes, rows, ", ".join(words.item(*item) for item in shared))
        milestones = [
            (key, milestone, texts.text(milestone.name).format(goal=texts.amount(milestone.goal)))
            for key, milestone in rules.milestones.items()
        ]
        chest = rules.first_chest
        named = game_text(game, words.catalogue, chest.name, language) if chest else ""
        return RewardsView(
            game,
            rules,
            words,
            year,
            words.dated(today(words.now, zone)),
            words.clock(day_start(today(words.now, zone), zone)),
            events,
            _strip(words, events, year),
            missions,
            packs,
            milestones,
            texts.text("web.admin.rewards.keys.first") if chest and named == chest.name else named,
        )


def event_row(words: RewardTexts, number: int, run: Run) -> EventRow:
    """Return an event of this year as the panel lists it.

    Args:
        words: Writes the page's language.
        number: Its place in the calendar, from 1.
        run: When it runs this year.
    """
    texts, rules, now = words.texts, words.rules, words.now
    event, last = run.event, today(run.period.end, rules.zone) - timedelta(days=1)
    day = event.window.day(now, rules.zone)
    if not event.enabled:
        state, said = "off", texts.text("web.admin.rewards.state.off")
    elif run.period.end <= now:
        state, said = "ended", texts.text("web.admin.rewards.state.ended")
    elif run.period.start > now:
        first = words.dated(today(run.period.start, rules.zone))
        state, said = "next", texts.text("web.admin.rewards.state.next", date=first)
    else:
        state = "paused" if rules.paused else "running"
        if event.daily and day:
            said = texts.text(f"web.admin.rewards.state.{state}.day", day=day, days=run.days)
        else:
            said = texts.text(f"web.admin.rewards.state.{state}.until", date=words.dated(last))
    first_day = words.dated(today(run.period.start, rules.zone))
    if any(part.goal for part in event.parts.values()):
        rule = texts.text("web.admin.rewards.rule.goals", date=words.dated(last + timedelta(1)))
    elif event.daily:
        rule = texts.count("web.admin.rewards.rule.daily", run.days, date=first_day)
    elif event.window.easter:
        rule = texts.text("web.admin.rewards.rule.easter")
    else:
        rule = texts.text("web.admin.rewards.rule.fixed")
    own = all(part.name.startswith("web.") for part in event.parts.values())
    return EventRow(run.key, number, run, state, said, words.event(run).dates, rule, own)


def _strip(words: RewardTexts, events: list[EventRow], year: int) -> Strip:
    # Each event on the first line where it fits after the events before it.
    zone = words.rules.zone
    january = date(year, 1, 1)
    ends: list[int] = []
    chips = []
    for row in sorted(events, key=lambda row: row.run.period.start):
        x = (today(row.run.period.start, zone) - january).days * DAY
        width = max(row.run.days * DAY, NARROWEST)
        line = next((index for index, end in enumerate(ends) if end < x), len(ends))
        ends[line : line + 1] = [x + width]
        chips.append(Chip(row, x, width, line))
    starts = [date(year, month, 1) for month in range(1, 13)]
    months = [
        ((first - january).days * DAY, words.day(day_start(first, zone), "MMM")) for first in starts
    ]
    width = (date(year + 1, 1, 1) - january).days * DAY
    now = (today(words.now, zone) - january).days * DAY + DAY // 2
    return Strip(width, max(len(ends), 1), months, chips, now)


def reward_routes(sessions: AdminSessions, views: RewardAdminViews) -> APIRouter:
    """Return the rewards of each game's site: its calendar, missions, packs and milestones.

    Every role reads them.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show.
    """
    router = APIRouter()

    @router.get("/admin/rewards")
    async def first(request: Request) -> Response:
        sessions.admin(request)
        if not views.games:
            raise HTTPException(404)
        return RedirectResponse(rewards_url(request, views.games[0]), 303)

    @router.get("/admin/rewards/{game_id}", response_class=HTMLResponse)
    async def rewards(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        found = await views.view(game, request.state.language)
        return await sessions.render(request, "admin.Rewards", admin=admin, found=found)

    return router


def rewards_url(request: Request, game: Game) -> str:
    """Return the address of a game's rewards in the admin panel, in the page's language.

    Args:
        request: The request, whose language the address takes.
        game: The game.
    """
    return localize(f"/admin/rewards/{game.id}", request.state.language)
