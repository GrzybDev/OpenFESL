import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.box_tables import BoxStore
from openfesl.core.catalogues import Catalogues, game_text
from openfesl.core.deal_picks import fit
from openfesl.core.deal_rules import DealSection, ItemSection, Offer, OfferSection
from openfesl.core.games import Game
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.deals import DealAdminViews, SectionRow
from openfesl.web.admin.store import SECTION, store_url
from openfesl.web.pages import whole_number
from openfesl.web.sessions import checked_form, or_404, toast
from openfesl.web.store_texts import StoreTexts

# The most deals of each faction, and the longest repeat gap in days, that a section takes.
MOST_SIZE = 50
MOST_GAP = 365
# Where an offer stands in its section's pool: picked among the others, never, or first.
STATES = ("in", "out", "pin")


@dataclass(frozen=True, slots=True)
class PoolEntry:
    """An offer of a section's pool, or offers picked together, as the section's page lists it.

    Attributes:
        picked: The name the section picks it by.
        name: Its name in the page's language, the first one's of offers picked together.
        faction: The faction whose heroes may buy it, any when none; empty when its items are
            not imported.
        count: How many items it holds, or for random boxes how many boxes are picked together.
        boxes: Whether it is random boxes rather than items.
        last: When the rotation last brought it, if it did.
        state: in, out or pin, as the section's settings set it.
    """

    picked: str
    name: str
    faction: str | None
    count: int
    boxes: bool
    last: datetime | None
    state: str


@dataclass(frozen=True, slots=True)
class SectionView:
    """A section of a game's hot-deal rotation as its page shows it, with its pool.

    Attributes:
        row: The section with its schedule and what it sells now.
        pool: Its offers, in the game data's order; none for a section of items.
        pins: Whether pinned offers come first, as the section picks a few at each step.
        zone: The time zone of the rotation's steps.
    """

    row: SectionRow
    pool: list[PoolEntry]
    pins: bool
    zone: str


class DealSectionViews:
    """Puts together the page of each section of the games' hot-deal rotations, with its pool."""

    def __init__(self, deals: DealAdminViews, catalogues: Catalogues, boxes: BoxStore) -> None:
        """Read the sections with the catalogues that name their offers and the stores' boxes.

        Args:
            deals: The games' hot-deal rotations.
            catalogues: The games' imported catalogues, which name the offers and their items.
            boxes: The stores' boxes, which tell whose heroes may open a box.
        """
        self.deals = deals
        self._catalogues = catalogues
        self._boxes = boxes

    async def section(self, game: Game, key: str, language: str) -> SectionView:
        """Return a section of a game's hot-deal rotation with its pool.

        Args:
            game: The game, which has hot deals.
            key: The section's name.
            language: The page's language.

        Raises:
            HTTPException: 404 for a section the rotation lacks.
        """
        rotation = await self.deals.rotation(game)
        row = or_404(next((row for row in rotation.rows if row.key == key), None))
        section = row.section
        zone = rotation.rules.zone
        if isinstance(section, ItemSection):
            return SectionView(row, [], False, zone)
        catalogue = await self._catalogues.of(game, language)
        boxes = {box.key: box.faction for box in await self._boxes.boxes(game.id)}
        shown = await self.deals.deals.shown(game, key)
        factions = list(game.heroes.factions) if game.heroes else []
        classes = list(game.heroes.classes) if game.heroes else []
        pool = []
        for picked, offers in _pools(rotation.rules.offers(section)).items():
            first = offers[0]
            items = [catalogue.item(item_id) for item_id, _ in first.members]
            found = [item for item in items if item is not None]
            if first.box:
                sides = {boxes.get(offer.box or "") for offer in offers}
                faction = sides.pop() if len(sides) == 1 else None
            else:
                fitting = fit(found, factions, classes) if len(found) == len(items) else None
                faction = fitting[0] if fitting else ""
            seen = [shown[offer.name] for offer in offers if offer.name in shown]
            pool.append(
                PoolEntry(
                    picked,
                    game_text(game, catalogue, first.name, language, faction or None),
                    faction,
                    len(offers) if first.box else len(first.members),
                    bool(first.box),
                    max(seen) if seen else None,
                    _state(section, picked),
                )
            )
        pins = section.size is not None and section.schedule is not None
        return SectionView(row, pool, pins, zone)


def section_routes(sessions: AdminSessions, views: DealSectionViews) -> APIRouter:
    """Return the page of each section of a game's hot-deal rotation, with its form and pool.

    Every role reads it; changing it needs the admin role. A change applies from the section's
    next step; what is on sale stays until then. Each offer of the pool is in it, pinned to come
    first at each step, also within the repeat gap, or excluded.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and change the sections.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/deals/sections/{key}"

    async def page(
        request: Request, admin: Admin, game: Game, key: str, form: Mapping[str, str], why: str
    ) -> HTMLResponse:
        found = await views.section(game, key, request.state.language)
        return await sessions.render(
            request,
            "admin.DealSection",
            400 if why else 200,
            admin=admin,
            of=game,
            found=found,
            form=form,
            why=why,
        )

    @router.get(path, response_class=HTMLResponse)
    async def section(request: Request, game_id: str, key: str) -> HTMLResponse:
        admin = sessions.admin(request)
        return await page(request, admin, views.deals.stores.game(game_id), key, {}, "")

    @router.post(path)
    async def change(request: Request, game_id: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        fields = {name: str(value).strip() for name, value in form.items()}
        game = views.deals.stores.game(game_id)
        found = await views.section(game, key, request.state.language)
        row = found.row
        texts = StoreTexts(game, request.state.language)
        try:
            values = _values(row.section, fields, [entry.picked for entry in found.pool])
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
            await views.deals.change(
                game, settings, AdminAction.DEAL_SECTION, target, details, admin
            )
        name = texts.text(f"games.{game.id}.deal_section.{key}")
        toast(request, "web.admin.toast.deals.section", section=name)
        return RedirectResponse(store_url(request, game, "/deals"), 303)

    return router


def _pools(offers: tuple[Offer, ...]) -> dict[str, list[Offer]]:
    # The offers by the name they are picked by, such as a family's boxes of both factions.
    pools: dict[str, list[Offer]] = {}
    for offer in offers:
        pools.setdefault(offer.picked, []).append(offer)
    return pools


def _state(section: OfferSection, picked: str) -> str:
    if picked in section.pinned:
        return "pin"
    return "out" if picked in section.excluded else "in"


def _fields(section: DealSection) -> dict[str, Any]:
    # The fields of a section that the panel changes, as the admin's log keeps them.
    fields: dict[str, Any] = {"enabled": section.enabled, "size": section.size}
    if isinstance(section, OfferSection):
        fields |= {"discount": list(section.discount), "gap": section.gap}
        fields |= {"pinned": list(section.pinned), "excluded": list(section.excluded)}
    else:
        fields["discount"] = section.discount
    return fields


def _values(section: DealSection, form: dict[str, str], pool: list[str]) -> dict[str, Any]:
    # The section's fields as the form sets them; a refusal raises ValueError with its name.
    def number(name: str, most: int) -> int:
        number = whole_number(form.get(name, "").removesuffix("%"), most, 0)
        if number is None:
            raise ValueError(name)
        return number

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
    states = [
        form.get(f"pool-{index}", _state(section, picked)) for index, picked in enumerate(pool)
    ]
    if any(state not in STATES for state in states):
        raise HTTPException(400)
    values["pinned"] = [
        picked for picked, state in zip(pool, states, strict=True) if state == "pin"
    ]
    values["excluded"] = [
        picked for picked, state in zip(pool, states, strict=True) if state == "out"
    ]
    return values


def _written(value: Any) -> str:
    # A field's value as the settings write it: on or off, a number, or a list as JSON.
    if isinstance(value, bool):
        return "on" if value else "off"
    return json.dumps(value) if isinstance(value, list) else str(value)
