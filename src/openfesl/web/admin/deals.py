from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.calendar import Window, today
from openfesl.core.catalogues import Catalogues, game_text
from openfesl.core.deal_rules import DealRules, DealSection, ItemSection, OfferSection
from openfesl.core.deal_steps import DealSteps, next_change, short
from openfesl.core.deal_uploads import DealUploads, offer_of
from openfesl.core.deals import Deal, HotDeals
from openfesl.core.games import Game
from openfesl.core.policies import SettingStore
from openfesl.core.store import AUTO, Bundle, ListedBundle
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import SECTION, StoreAdminViews, store_url
from openfesl.web.deal_art import DealArt
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

# How many deals the game's home screen shows, of which the first ones big.
HOME = 5
BIG = 2


@dataclass(frozen=True, slots=True)
class HomeDeal:
    """A hot deal as the game's home screen shows it, with the pictures it downloads.

    Attributes:
        deal: The deal, named in the page's language.
        offer: The name of the offer it sells, which uploaded pictures go with.
        big: Whether the home screen shows its big picture rather than its small one.
        uploaded: Which of its pictures were uploaded: True for the big one, False for the small.
    """

    deal: Deal
    offer: str
    big: bool
    uploaded: set[bool]


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
class StepView:
    """A section's next step as the rotation page previews it, a line for each deal.

    Attributes:
        comes: The deals it brings that are not on sale now.
        leaves: The deals on sale now that it ends and does not bring again.
        short: The factions it brings fewer deals of than the section's size.
    """

    comes: list[str]
    leaves: list[str]
    short: list[str]


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

    def __init__(
        self,
        stores: StoreAdminViews,
        deals: HotDeals,
        steps: DealSteps,
        settings: SettingStore,
        art: DealArt,
        uploads: DealUploads,
        catalogues: Catalogues,
    ) -> None:
        """Read the rotations and change them through the instance's settings.

        Args:
            stores: Name the games with stores.
            deals: The hot deals, which run the rotations.
            steps: The rotations' steps, of which the page previews the next.
            settings: The instance's settings, which override the rotations' game data.
            art: Draws the deals' pictures, or gives the uploaded ones.
            uploads: Where the pictures that the admin uploaded are kept.
            catalogues: The games' imported catalogues, which name the deals of a preview.
        """
        self.stores = stores
        self.steps = steps
        self._catalogues = catalogues
        self._deals = deals
        self._settings = settings
        self.art = art
        self.uploads = uploads

    async def home(self, game: Game, language: str) -> list[HomeDeal]:
        """Return the hot deals that the game's home screen shows, with their pictures.

        Args:
            game: The game, which has hot deals.
            language: The language of the deals' names.
        """
        uploaded = await self.uploads.uploaded(game.id)
        shown = (await self._deals.hot_deals(game, language))[:HOME]
        return [
            HomeDeal(
                deal,
                offer_of(deal.listed.bundle),
                index < BIG,
                uploaded.get(offer_of(deal.listed.bundle), set()),
            )
            for index, deal in enumerate(shown)
        ]

    async def rules(self, game: Game) -> DealRules:
        """Return how a game's hot deals change, with the instance's settings over its data.

        Args:
            game: The game.

        Raises:
            HTTPException: 404 for a game without hot deals.
        """
        rules = await self._deals.rules(game)
        if rules is None:
            raise HTTPException(404)
        return rules

    async def rotation(self, game: Game) -> Rotation:
        """Return a game's hot-deal rotation with its sections and what they sell now.

        Args:
            game: The game, which has hot deals.

        Raises:
            HTTPException: 404 for a game without hot deals.
        """
        rules = await self.rules(game)
        assert game.deals is not None
        listed = await self._deals.current(game)
        now = datetime.now(UTC)
        rows = []
        for key, section in rules.sections.items():
            mine = [found for found in listed if found.bundle.section == key]
            own = [found.bundle for found in mine if found.bundle.source == AUTO]
            latest = max((bundle.starts for bundle in own if bundle.starts), default=None)
            runs = rules.enabled and section.enabled and latest is not None
            fewer = short(game, section, [b for b in own if b.starts == latest]) if runs else {}
            rows.append(
                SectionRow(
                    key,
                    section,
                    game.deals.sections[key],
                    len(mine),
                    next_change(rules, section, now),
                    fewer,
                )
            )
        return Rotation(rules, game.deals, rows, _seasons(rules, now), listed)

    async def preview(self, game: Game, language: str) -> dict[str, StepView]:
        """Return what each running section's next step brings and ends, by section.

        A deal that the step ends and brings again is neither.

        Args:
            game: The game, which has hot deals.
            language: The language of the deals' names.
        """
        rules = await self.rules(game)
        now = datetime.now(UTC)
        texts = StoreTexts(game, language, now)
        catalogue = await self._catalogues.of(game, language)
        whose = {
            faction: texts.factions(frozenset({faction}))
            for faction in (game.heroes.factions if game.heroes else ())
        }

        def line(
            section: DealSection, bundle: Bundle, items: list[int]
        ) -> tuple[tuple[object, ...], str]:
            # A deal by what tells it from others and in words.
            if isinstance(section, ItemSection):
                name = ", ".join(catalogue.name(item) or str(item) for item in items)
            else:
                name = game_text(game, catalogue, bundle.name, language, bundle.faction)
            said = texts.text(
                "web.admin.deals.preview.line",
                name=name,
                whom=whose.get(bundle.faction or "", texts.text("web.admin.deals.pool.any")),
                price=texts.priced(bundle.price, bundle.currency),
            )
            return (bundle.pool, bundle.faction, tuple(sorted(items))), said

        found = {}
        for key, step in (await self.steps.preview(game)).items():
            section = rules.sections[key]
            new = [
                line(section, deal.bundle, [product.item_id for product in deal.products])
                for deal in step.deals
            ]
            old = [
                line(section, ending.bundle, [member.item_id for member in ending.members])
                for ending in step.ending
            ]
            made, kept = {deal for deal, _ in new}, {deal for deal, _ in old}
            found[key] = StepView(
                [said for deal, said in new if deal not in kept],
                [said for deal, said in old if deal not in made],
                [
                    texts.text(
                        "web.admin.deals.preview.short",
                        faction=whose[faction],
                        count=texts.amount(count),
                        size=texts.amount(section.size or 0),
                    )
                    for faction, count in step.short.items()
                ],
            )
        return found

    async def shown(self, game: Game, section: str) -> dict[str, datetime]:
        """Return when the rotation last brought each offer of a section, by its picked name.

        Args:
            game: The game.
            section: The section's name.
        """
        return await self._deals.shown(game, section)

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

    @router.get("/admin/store/{game_id}/deals", response_class=HTMLResponse)
    async def deals(request: Request, game_id: str, preview: bool = False) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.stores.game(game_id)
        return await deal_page(sessions, views, request, admin, game, preview=preview)

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

    return router


async def deal_page(
    sessions: AdminSessions,
    views: DealAdminViews,
    request: Request,
    admin: Admin,
    game: Game,
    art: tuple[int, str] | None = None,
    preview: bool = False,
) -> HTMLResponse:
    """Render a game's hot-deal rotation page, with a refused picture if there is one.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the page shows.
        request: The request the page answers.
        admin: The signed-in admin.
        game: The game, which has hot deals.
        art: The deal whose uploaded picture was refused, by its bundle's number, and why.
        preview: Whether the page shows what each section's next step brings and ends.
    """
    language = request.state.language
    return await sessions.render(
        request,
        "admin.StoreDeals",
        400 if art else 200,
        admin=admin,
        of=game,
        rotation=await views.rotation(game),
        steps=await views.preview(game, language) if preview else None,
        home=await views.home(game, language),
        art=art,
        manages=admin.role is Role.ADMIN,
    )


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
