from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.box_tables import BoxStore, expected
from openfesl.core.calendar import Window, today
from openfesl.core.catalogues import Catalogue, Catalogues, game_text
from openfesl.core.claw import Claw
from openfesl.core.deal_rules import OfferSection
from openfesl.core.deals import HotDeals
from openfesl.core.drops import Box, below_value, worth
from openfesl.core.games import Game
from openfesl.core.policies import SettingStore
from openfesl.core.store import ListedBundle, Product
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.claw_panel import ClawForm, claw_panel
from openfesl.web.admin.store import SECTION, StoreAdminViews, store_url
from openfesl.web.sessions import checked_form, or_404, toast
from openfesl.web.store_texts import StoreTexts

# The group of the boxes that no section of the hot deals sells, and of the treasure chests.
OTHER = "unlisted"
CHEST = "chest"


@dataclass(frozen=True, slots=True)
class BoxRow:
    """A box of a game's store as its boxes and chests list it.

    Attributes:
        box: The box with its drops.
        name: Its name, as the hot deals' offer that sells it names it.
        section: The hot deals' section that sells it; none for one no section sells.
        days: The first and last days of this year on which its section sells it, if they are
            fixed.
        always: Whether its section sells it in every period.
        promised: Whether the text of the offer that sells it promises its worth, which the
            game's texts may; such a box keeps its promise.
        selling: Its hot deal on sale now, if one is.
        ratio: What it gives on average against its price, if it gives anything of value.
        below: Its drops worth less than its price, which rolls skip while it promises its worth.
    """

    box: Box
    name: str
    section: str | None
    days: tuple[datetime, datetime] | None
    always: bool
    promised: bool
    selling: ListedBundle | None
    ratio: Fraction | None
    below: list[Product]


@dataclass(frozen=True, slots=True)
class BoxFlag:
    """A drop of a box that is worth less than the box's price, as Admin Home shows it.

    Attributes:
        game: The box's game.
        row: The box.
        product: The drop.
        item: The drop's item's name.
    """

    game: Game
    row: BoxRow
    product: Product
    item: str


@dataclass(frozen=True, slots=True)
class Entry:
    """A drop of a box as its drop table shows it.

    Attributes:
        product: The product it gives.
        name: The product's item's name.
        counts: Whether rolls give it, as it keeps the box's promise.
    """

    product: Product
    name: str
    counts: bool


@dataclass(frozen=True, slots=True)
class TierView:
    """A tier of a box's drop table.

    Attributes:
        index: Its number, from 0.
        weight: How likely it is against the others.
        share: Its share of the rolls, in percent.
        entries: Its drops.
    """

    index: int
    weight: int
    share: Fraction
    entries: list[Entry]


class BoxAdminViews:
    """Puts together what the admin panel shows of the boxes and chests of the games' stores."""

    def __init__(
        self,
        stores: StoreAdminViews,
        catalogues: Catalogues,
        deals: HotDeals,
        boxes: BoxStore,
        claw: Claw,
        settings: SettingStore,
    ) -> None:
        """Read the boxes, the hot deals that sell them and the catalogues that name them.

        Args:
            stores: Name the games with stores.
            catalogues: The games' imported catalogues.
            deals: The hot deals, whose offers name and sell the boxes.
            boxes: Where the boxes and their drop tables are kept.
            claw: Runs the games' Daily Claws, whose prizes are a box.
            settings: The instance's settings, which change the Claws.
        """
        self.stores = stores
        self.claw = claw
        self._catalogues = catalogues
        self.deals = deals
        self.boxes = boxes
        self._settings = settings

    async def rows(self, game: Game, language: str) -> tuple[Catalogue, list[BoxRow]]:
        """Return a game's catalogue and its store's boxes, those the hot deals sell first.

        Args:
            game: The game, which has a store.
            language: The language of the boxes' names.
        """
        catalogue = await self._catalogues.of(game, language)
        rules = await self.deals.rules(game)
        offered: dict[str, tuple[str, str, Window | None, bool, bool]] = {}
        for key, section in rules.sections.items() if rules else []:
            if isinstance(section, OfferSection):
                every = section.size is None and section.schedule is not None
                for offer in section.offers:
                    if offer.box and offer.box not in offered:
                        always = every and offer.window is None
                        promised = offer.description is not None
                        offered[offer.box] = (offer.name, key, offer.window, always, promised)
        selling = {
            listed.bundle.box: listed
            for listed in await self.deals.current(game)
            if listed.bundle.box is not None
        }
        order = list(rules.sections) if rules else []
        zone = rules.zone if rules else "UTC"
        year = today(datetime.now(UTC), zone).year
        rows = []
        for box in await self.boxes.boxes(game.id):
            text, shelf, window, always, promised = offered.get(
                box.key, ("", "", None, False, False)
            )
            texts = StoreTexts(game, language)
            if text:
                name = game_text(game, catalogue, text, language)
            elif box.kind == "claw" and game.claw:
                name = catalogue.text(game.claw.name) or texts.text("web.claw.name")
            else:
                name = texts.text(f"web.admin.boxes.{box.kind}")
            period = window.period(year, zone) if window else None
            rows.append(
                BoxRow(
                    box,
                    name,
                    shelf or None,
                    (period.start, period.end - timedelta(days=1)) if period else None,
                    always,
                    promised,
                    selling.get(box.id),
                    expected(box),
                    below_value(box),
                )
            )
        rows.sort(
            key=lambda row: (
                row.box.kind != "box",
                order.index(row.section) if row.section in order else len(order),
            )
        )
        return catalogue, rows

    async def change_claw(
        self, game: Game, settings: dict[str, str | None], details: dict[str, Any], admin: Admin
    ) -> None:
        """Change a game's Claw's settings together and record them as one action.

        Args:
            game: The game, which is the action's target.
            settings: The settings' new values by key below the game's Claw; None removes one.
            details: What the admin's log keeps of the change.
            admin: The admin who changed them.
        """
        keyed = {f"{game.id}.claw.{key}": value for key, value in settings.items()}
        await self._settings.change_all(keyed, AdminAction.CLAW_SETTINGS, game.id, details, admin)

    async def flags(self, language: str) -> list[BoxFlag]:
        """Return the drops of every store's boxes that are worth less than their box's price.

        Args:
            language: The language of the names.
        """
        found = []
        for game in self.stores.games:
            catalogue, rows = await self.rows(game, language)
            for row in rows:
                for product in row.below:
                    name = catalogue.name(product.item_id) or str(product.item_id)
                    found.append(BoxFlag(game, row, product, name))
        return found

    async def flagged(self) -> int:
        """Return how many drops of the stores' boxes are worth less than their box's price."""
        count = 0
        for game in self.stores.games:
            count += sum(len(below_value(box)) for box in await self.boxes.boxes(game.id))
        return count


def tiers(box: Box, catalogue: Catalogue) -> list[TierView]:
    """Return a box's drop table by tier, each with its share of the rolls.

    Args:
        box: The box.
        catalogue: The game's catalogue, which names the items.
    """
    found: dict[int, TierView] = {}
    for drop in box.drops:
        product = drop.product
        name = catalogue.name(product.item_id) or str(product.item_id)
        entry = Entry(product, name, worth(box, product))
        if drop.tier not in found:
            found[drop.tier] = TierView(drop.tier, drop.weight, Fraction(0), [])
        found[drop.tier].entries.append(entry)
    total = sum(tier.weight for tier in found.values())
    return [
        TierView(tier.index, tier.weight, Fraction(100 * tier.weight, total), tier.entries)
        for tier in sorted(found.values(), key=lambda tier: tier.index)
    ]


async def boxes_page(
    sessions: AdminSessions,
    views: BoxAdminViews,
    request: Request,
    admin: Admin,
    game: Game,
    claw: ClawForm | None = None,
) -> HTMLResponse:
    """Return a game's boxes and chests page, with its Claw as saved or as a refused save sent it.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the page shows.
        request: The request the page answers.
        admin: The admin who reads it.
        game: The game, which has a store.
        claw: The Claw's settings' form of a refused save; the saved settings when none.
    """
    language = request.state.language
    catalogue, rows = await views.rows(game, language)
    groups: dict[str, list[BoxRow]] = {}
    # The Claw's prizes are no box players buy or open; the Claw has its own entry.
    for row in (row for row in rows if row.box.kind != "claw"):
        key = CHEST if row.box.kind != "box" else row.section or OTHER
        groups.setdefault(key, []).append(row)
    return await sessions.render(
        request,
        "admin.StoreBoxes",
        400 if claw else 200,
        admin=admin,
        of=game,
        catalogue=catalogue,
        groups=groups,
        deals=await views.deals.rules(game),
        chests=(await views.stores.account_rules(game)).chests,
        claw=await claw_panel(views.claw, game, catalogue, language, claw),
    )


def box_routes(sessions: AdminSessions, views: BoxAdminViews) -> APIRouter:
    """Return the boxes and chests of each game's store, and their switches.

    Every role reads them; switching a box off sale or the opening of chests off needs the admin
    role, applies at once and is recorded among the admin's actions.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show.
    """
    router = APIRouter()

    @router.get("/admin/store/{game_id}/boxes", response_class=HTMLResponse)
    async def boxes(request: Request, game_id: str) -> HTMLResponse:
        game = views.stores.game(game_id)
        return await boxes_page(sessions, views, request, sessions.admin(request), game)

    @router.post("/admin/store/{game_id}/boxes/{box_id}/switch")
    async def switch(request: Request, game_id: str, box_id: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = views.stores.game(game_id)
        _, rows = await views.rows(game, request.state.language)
        row = or_404(next((row for row in rows if row.box.id == box_id), None))
        on = bool(form.get("enabled"))
        if on != row.box.enabled:
            await views.boxes.switch(game.id, row.box, on, admin)
        kind = "box" if row.box.kind == "box" else "chest"
        toast(request, f"web.admin.toast.boxes.{kind}.{'on' if on else 'off'}", box=row.name)
        back = back_to(request, str(form.get("back", "")), store_url(request, game, "/boxes"))
        return RedirectResponse(back, 303)

    return router
