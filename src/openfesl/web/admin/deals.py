import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.calendar import Window, next_start, today
from openfesl.core.deal_rules import DealRules, DealSection, ItemSection, OfferSection
from openfesl.core.deals import HotDeals
from openfesl.core.games import Game
from openfesl.core.policies import SettingStore
from openfesl.core.store import AUTO, ListedBundle
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import SECTION, StoreAdminViews, store_url
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

# The most deals of each faction, and the longest repeat gap in days, that a section takes.
MOST_SIZE = 50
MOST_GAP = 365


@dataclass(frozen=True, slots=True)
class SectionRow:
    """A section of a game's hot-deal rotation as the panel lists it.

    Attributes:
        key: Its name in the game's data.
        section: The section as the instance sets it.
        seed: The section as the game's data gives it.
        now: How many of its deals are on sale now.
        next: When its next period starts, for a section with a schedule.
        short: The factions whose deals its latest period has fewer of than its size asks, with
            how many it has.
    """

    key: str
    section: DealSection
    seed: DealSection
    now: int
    next: datetime | None
    short: dict[str, int]


@dataclass(frozen=True, slots=True)
class SeasonView:
    """A window of each year in which sections sell offers, as the panel lists it.

    Attributes:
        first: Its first day this year.
        last: Its last day this year.
        state: ended, running, next or later.
        names: The ids of the texts that name its offers.
    """

    first: datetime
    last: datetime
    state: str
    names: list[str]


@dataclass(frozen=True, slots=True)
class Rotation:
    """A game's hot-deal rotation as its page in the panel shows it.

    Attributes:
        rules: The rotation as the instance sets it.
        data: The rotation as the game's data gives it.
        rows: Its sections.
        seasons: The windows of this year in which sections sell offers.
        listed: The bundles on hot deals now.
    """

    rules: DealRules
    data: DealRules
    rows: list[SectionRow]
    seasons: list[SeasonView]
    listed: list[ListedBundle]


class DealAdminViews:
    """Puts together what the admin panel shows of the games' hot-deal rotations."""

    def __init__(self, stores: StoreAdminViews, deals: HotDeals, settings: SettingStore) -> None:
        """Read the rotations and change them through the instance's settings.

        Args:
            stores: Name the games with stores.
            deals: The hot deals, which run the rotations.
            settings: The instance's settings, which override the rotations' game data.
        """
        self.stores = stores
        self._deals = deals
        self._settings = settings

    async def rotation(self, game: Game) -> Rotation:
        """Return a game's hot-deal rotation with its sections and what they sell now.

        Args:
            game: The game, which has hot deals.

        Raises:
            HTTPException: 404 for a game without hot deals.
        """
        rules = await self._deals.rules(game)
        if rules is None or game.deals is None:
            raise HTTPException(404)
        listed = await self._deals.current(game)
        now = datetime.now(UTC)
        factions = list(game.heroes.factions) if game.heroes else []
        rows = []
        for key, section in rules.sections.items():
            mine = [found for found in listed if found.bundle.section == key]
            schedule = section.schedule
            upcoming = next_start(schedule, now, rules.zone) if schedule else None
            rows.append(
                SectionRow(
                    key,
                    section,
                    game.deals.sections[key],
                    len(mine),
                    upcoming,
                    _short(section, mine, factions) if rules.enabled and section.enabled else {},
                )
            )
        return Rotation(rules, game.deals, rows, _seasons(rules, now), listed)

    async def short(self) -> list[tuple[Game, SectionRow]]:
        """Return the sections of the rotations whose latest period sells fewer deals than asked."""
        found = []
        for game in self.stores.games:
            if game.deals is None:
                continue
            rotation = await self.rotation(game)
            found += [(game, row) for row in rotation.rows if row.short]
        return found

    async def change(
        self,
        game: Game,
        settings: dict[str, str | None],
        action: AdminAction,
        target: str,
        details: dict[str, Any],
        admin: Admin,
    ) -> None:
        """Change a rotation's settings together and record them as one action.

        Args:
            game: The game.
            settings: The settings' new values by key below the game's deals; None removes one.
            action: What the admin did.
            target: What it was done to.
            details: What the admin's log keeps of it.
            admin: The admin who changed them.
        """
        prefix = f"{game.id}.deals."
        keyed = {prefix + key: value for key, value in settings.items()}
        await self._settings.change_all(keyed, action, target, details, admin)


def deal_routes(sessions: AdminSessions, views: DealAdminViews) -> APIRouter:
    """Return the hot-deal rotation of each game's store, which admins switch and change.

    Every role reads it; changing it needs the admin role. Changes apply from each section's
    next period; what is on sale stays until then.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and change the rotations.
    """
    router = APIRouter()

    async def page(
        request: Request, admin: Admin, game: Game, key: str, form: dict[str, str], why: str
    ) -> HTMLResponse:
        rotation = await views.rotation(game)
        chosen = next((row for row in rotation.rows if row.key == key), rotation.rows[0])
        return await sessions.render(
            request,
            "admin.StoreDeals",
            400 if why else 200,
            admin=admin,
            of=game,
            rotation=rotation,
            chosen=chosen,
            form=form,
            why=why,
            manages=admin.role is Role.ADMIN,
        )

    @router.get("/admin/store/{game_id}/deals", response_class=HTMLResponse)
    async def deals(request: Request, game_id: str, section: str = "") -> HTMLResponse:
        admin = sessions.admin(request)
        return await page(request, admin, views.stores.game(game_id), section, {}, "")

    @router.post("/admin/store/{game_id}/deals/rotation")
    async def switch(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.stores.game(game_id)
        rotation = await views.rotation(game)
        on = bool(form.get("enabled"))
        if on != rotation.rules.enabled:
            value = None if on == rotation.data.enabled else ("on" if on else "off")
            details = {"enabled": on}
            await views.change(
                game, {"enabled": value}, AdminAction.DEAL_ROTATION, game.id, details, admin
            )
        toast(request, f"web.admin.toast.deals.rotation.{'on' if on else 'off'}")
        return RedirectResponse(store_url(request, game, "/deals"), 303)

    @router.post("/admin/store/{game_id}/deals/sections/{key}")
    async def change(request: Request, game_id: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        fields = {name: str(value).strip() for name, value in form.items()}
        game = views.stores.game(game_id)
        rotation = await views.rotation(game)
        row = next((row for row in rotation.rows if row.key == key), None)
        if row is None:
            raise HTTPException(404)
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        try:
            values = _values(row.section, fields)
        except ValueError as refused:
            why = texts.text(f"web.admin.deals.refused.{refused}", most=texts.amount(MOST_SIZE))
            return await page(request, admin, game, key, fields, why)
        before, seed = _fields(row.section), _fields(row.seed)
        settings = {
            f"sections.{key}.{name}": None if value == seed[name] else _written(value)
            for name, value in values.items()
            if value != before[name]
        }
        if settings:
            details = {"game": game.id, "section": key, "before": before, "after": values}
            target = f"{game.id}:{key}"
            await views.change(game, settings, AdminAction.DEAL_SECTION, target, details, admin)
        name = texts.text(f"games.{game.id}.deal_section.{key}")
        toast(request, "web.admin.toast.deals.section", section=name)
        return RedirectResponse(store_url(request, game, f"/deals?section={key}"), 303)

    return router


def _fields(section: DealSection) -> dict[str, Any]:
    # The fields of a section that the panel changes, as the admin's log keeps them.
    fields: dict[str, Any] = {"enabled": section.enabled, "size": section.size}
    if isinstance(section, OfferSection):
        fields |= {"discount": list(section.discount), "gap": section.gap}
    else:
        fields["discount"] = section.discount
    return fields


def _values(section: DealSection, form: dict[str, str]) -> dict[str, Any]:
    # The section's fields as the form sets them; a refusal raises ValueError with its name.
    def number(name: str, most: int) -> int:
        text = form.get(name, "").removesuffix("%").strip()
        if not (text.isascii() and text.isdecimal()) or int(text) > most:
            raise ValueError(name)
        return int(text)

    values: dict[str, Any] = {"enabled": bool(form.get("enabled"))}
    values["size"] = number("size", MOST_SIZE) if section.size is not None else None
    if values["size"] == 0:
        raise ValueError("size")
    if isinstance(section, ItemSection):
        values["discount"] = number("discount", 99)
        return values
    low, high = number("low", 99), number("high", 99)
    if low > high:
        raise ValueError("range")
    values["discount"] = [low, high]
    values["gap"] = number("gap", MOST_GAP)
    return values


def _written(value: Any) -> str:
    # A field's value as the settings write it: on or off, a number, or a list as JSON.
    if isinstance(value, bool):
        return "on" if value else "off"
    return json.dumps(value) if isinstance(value, list) else str(value)


def _short(section: DealSection, listed: list[ListedBundle], factions: list[str]) -> dict[str, int]:
    # The factions that the section's latest period sells fewer of its own deals to than its
    # size asks, where it picks a few of each faction's.
    if section.size is None or (isinstance(section, OfferSection) and not section.sets):
        return {}
    own = [found for found in listed if found.bundle.source == AUTO]
    if not own:
        return {}
    latest = max(found.bundle.starts or datetime.min.replace(tzinfo=UTC) for found in own)
    counts = {faction: 0 for faction in factions}
    for found in own:
        if found.bundle.starts == latest and found.bundle.faction in counts:
            counts[found.bundle.faction] += 1
    return {faction: count for faction, count in counts.items() if count < section.size}


def _seasons(rules: DealRules, now: datetime) -> list[SeasonView]:
    # The windows of this year in which sections sell offers, in the order of the year.
    windows: dict[Window, list[str]] = {}
    for section in rules.sections.values():
        if isinstance(section, OfferSection) and section.enabled:
            for offer in rules.offers(section):
                if offer.window is not None:
                    windows.setdefault(offer.window, []).append(offer.name)
    year = today(now, rules.zone).year
    found = []
    for window, names in windows.items():
        period = window.period(year, rules.zone)
        found.append((period.start, period.end, names))
    found.sort(key=lambda found: found[0])
    upcoming = next((start for start, _, _ in found if start > now), None)
    views = []
    for start, end, names in found:
        state = (
            "ended"
            if end <= now
            else "running"
            if start <= now
            else "next"
            if start == upcoming
            else "later"
        )
        views.append(SeasonView(start, end - timedelta(days=1), state, names))
    return views
