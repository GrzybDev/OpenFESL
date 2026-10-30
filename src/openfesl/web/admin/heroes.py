from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Role
from openfesl.core.catalogues import Catalogue, CatalogueFiles, Catalogues, file_path
from openfesl.core.chests import key_tag
from openfesl.core.entitlements import ACTIVE, Entitlement, EntitlementQuery, EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.hero_admin import HeroAdminStore, HeroHistory, ItemSource, next_default
from openfesl.core.hero_items import granted, holding
from openfesl.core.heroes import Hero, Heroes, Roster
from openfesl.core.inventory import COUNTED, PERMANENT, Held
from openfesl.core.mugshots import Mugshots
from openfesl.core.store_window import left_of, runs_low
from openfesl.core.wallets import Wallets
from openfesl.web.admin.accounts import AccountView, AccountViews, PersonaView
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.heroes import HeroCard, hero_cards
from openfesl.web.profiles import seen_by_others

# The inventory's tabs: the items a hero holds now, those it held, and both.
TABS = ("active", "ended", "all")


@dataclass(frozen=True, slots=True)
class GameHeroes:
    """An account's heroes in one game, as its page in the admin panel lists them.

    Attributes:
        game: The game, which has hero rules.
        roster: The heroes, with the account's slots and default hero.
        cards: What each hero's row shows, in the roster's order.
        keys: The treasure chest keys the account holds; none in a game without chests.
    """

    game: Game
    roster: Roster
    cards: list[HeroCard]
    keys: int | None


@dataclass(frozen=True, slots=True)
class InventoryRow:
    """An item a hero holds or held, as its page in the admin panel lists it.

    Attributes:
        item_id: The item's number.
        held: How the hero holds it, or held it last.
        entry: The entitlement kept for it; none for an item of the loadout, which the game
            grants the hero itself.
        source: Where it came from, where a record or the loadout says.
        state: active, expired, used_up, revoked (by an admin) or out_of_use (by its game).
        ended: When it ended, where that is known.
        low: Whether what is left of it runs low.
    """

    item_id: int
    held: Held
    entry: Entitlement | None
    source: ItemSource | None
    state: str
    ended: datetime | None
    low: bool


@dataclass(frozen=True, slots=True)
class HeroPage:
    """One hero as its page in the admin panel shows it.

    Attributes:
        game: The hero's game.
        view: The account it belongs to.
        roster: The account's heroes.
        card: What the hero's row on the account's page shows.
        catalogue: The game's catalogue in the page's language.
        history: What the records keep of the hero's items and mugshot.
        items: The items it holds and held, the loadout's first.
        persona: The hero's persona as the account's page lists it, with where it plays.
    """

    game: Game
    view: AccountView
    roster: Roster
    card: HeroCard
    catalogue: Catalogue
    history: HeroHistory
    items: list[InventoryRow]
    persona: PersonaView | None

    @property
    def public(self) -> bool:
        """Whether others see the hero's public profile on its game's site."""
        return seen_by_others(self.view.record.account) and not self.card.hero.disabled

    @property
    def next_default(self) -> Hero | None:
        """The hero that becomes the account's default once this one, the default, is disabled."""
        return next_default(self.roster, self.card.hero)

    @property
    def counts(self) -> dict[str, int]:
        """How many items each of the inventory's tabs lists."""
        active = sum(row.state == "active" for row in self.items)
        return {"active": active, "ended": len(self.items) - active, "all": len(self.items)}


class HeroAdminViews:
    """Puts together what the admin panel shows about the heroes of accounts."""

    def __init__(
        self,
        games: Games,
        heroes: Heroes,
        wallets: Wallets,
        entitlements: EntitlementStore,
        catalogues: Catalogues,
        store: HeroAdminStore,
    ) -> None:
        """Gather from the heroes, their wallets, items and records.

        Args:
            games: The games, of which those with hero rules have heroes.
            heroes: Lists each account's heroes.
            wallets: Reads the heroes' Hero Points without opening their wallets.
            entitlements: Lists the items, chests and keys kept for heroes and accounts.
            catalogues: The games' imported catalogues, which name the items.
            store: What is recorded of heroes.
        """
        self.games = [game for game in games if game.heroes]
        self.heroes = heroes
        self._wallets = wallets
        self._entitlements = entitlements
        self._catalogues = catalogues
        self.store = store

    async def of_account(self, account_id: int, language: str) -> list[GameHeroes]:
        """Return an account's heroes in each game with hero rules.

        Args:
            account_id: The account.
            language: The page's language, which names the items.
        """
        found = []
        for game in self.games:
            roster = await self.heroes.roster(game, account_id)
            catalogue = await self._catalogues.of(game, language)
            cards = await hero_cards(game, roster, catalogue, self._wallets, self._entitlements)
            keys = None
            if game.chests:
                query = EntitlementQuery(tag=key_tag(game.chests), status=ACTIVE)
                held = await self._entitlements.of_account(account_id, query)
                keys = sum(entry.uses or 0 for entry in held)
            found.append(GameHeroes(game, roster, cards, keys))
        return found

    async def page(self, view: AccountView, hero_id: int, language: str) -> HeroPage | None:
        """Return the page of one of an account's heroes, if it has the hero.

        Args:
            view: The account.
            hero_id: The hero's persona.
            language: The page's language, which names the items.
        """
        for found in await self.of_account(view.record.account.id, language):
            for card in found.cards:
                if card.hero.persona.id == hero_id:
                    return await self._page(found, card, view, language)
        return None

    async def _page(
        self, found: GameHeroes, card: HeroCard, view: AccountView, language: str
    ) -> HeroPage:
        game, hero = found.game, card.hero
        rules = game.heroes
        assert rules is not None
        history = await self.store.history(hero.persona.id)
        kept = await self._entitlements.of_persona(hero.persona.id)
        loadout = granted(rules, hero.faction, hero.hero_class)
        starting = {int(tag.partition("-")[0]) for tag in rules.starting_items}
        now = datetime.now(UTC)
        rows = [
            InventoryRow(
                item, Held(PERMANENT), None, ItemSource.STARTING_KIT, "active", None, False
            )
            for item in sorted(loadout - {_number(entry.tag) for entry in kept})
        ]
        for entry in kept:
            item = _number(entry.tag)
            if item is None:
                continue
            held = holding(item, [entry])
            state, ended = _state(entry, held, now, history.revoked.get(item))
            left = left_of(held, now) if state == "active" else None
            source = history.sources.get(item)
            if source is None and (item in loadout or item in starting):
                source = ItemSource.STARTING_KIT
            rows.append(
                InventoryRow(item, held, entry, source, state, ended, runs_low(left, rules))
            )
        persona = next(
            (entry for entry in view.personas if entry.record.persona.id == hero.persona.id), None
        )
        catalogue = await self._catalogues.of(game, language)
        return HeroPage(game, view, found.roster, card, catalogue, history, rows, persona)


def hero_routes(
    sessions: AdminSessions,
    accounts: AccountViews,
    views: HeroAdminViews,
    catalogues: Catalogues,
    files: CatalogueFiles | None,
    mugshots: Mugshots,
) -> APIRouter:
    """Return each hero's page, and the item icons and mugshots that the panel's pages show.

    Every role opens them; the panel shows the icons of withheld items too, and mugshots
    whoever the game's art is shown to.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        accounts: Puts together what the panel shows about the accounts.
        views: Puts together what the panel shows about the heroes.
        catalogues: The games' imported catalogues, which name the icons.
        files: Where the icons are kept; none where the deployment keeps no files.
        mugshots: Where the mugshots are kept.
    """
    router = APIRouter()
    games = {game.id: game for game in views.games}

    @router.get("/admin/accounts/{account_id}/heroes/{hero_id}", response_class=HTMLResponse)
    async def hero(request: Request, account_id: str, hero_id: int, items: str = "") -> Response:
        admin = sessions.admin(request)
        view = await accounts.find(account_id)
        page = await views.page(view, hero_id, request.state.language) if view else None
        if page is None:
            raise HTTPException(404)
        return await sessions.render(
            request,
            "admin.Hero",
            admin=admin,
            page=page,
            tab=items if items in TABS else TABS[0],
            manages=admin.role is Role.ADMIN,
        )

    @router.get("/admin/icons/{game}/{item_id}")
    async def icon(request: Request, game: str, item_id: int) -> Response:
        sessions.admin(request)
        known = games.get(game)
        contents = catalogues.contents(known) if known else None
        item = contents.items.get(item_id) if contents else None
        if files is None or contents is None or contents.revision is None or not item:
            raise HTTPException(404)
        located = await files.located(game, contents.revision, file_path("icons", item.icon or ""))
        if located is None:
            raise HTTPException(404)
        return FileResponse(
            located, media_type="image/webp", headers={"Cache-Control": "private, max-age=600"}
        )

    @router.get("/admin/mugshots/{game}/{hero_id}/{version}.png")
    async def mugshot(request: Request, game: str, hero_id: int, version: int) -> Response:
        sessions.admin(request)
        known = games.get(game)
        found = await mugshots.image(known, hero_id) if known else None
        if found is None or found[1] != version:
            raise HTTPException(404)
        return Response(found[0], media_type="image/png", headers={"Cache-Control": "private"})

    return router


def _number(tag: str) -> int | None:
    # The item a tag names; none for what is neither worn nor used, such as a treasure chest.
    number = tag.partition("-")[0]
    return int(number) if number.isascii() and number.isdecimal() else None


def _state(
    entry: Entitlement, held: Held, now: datetime, revoked: datetime | None
) -> tuple[str, datetime | None]:
    if entry.status != ACTIVE:
        return ("revoked", revoked) if revoked else ("out_of_use", None)
    if held.type == COUNTED and not held.uses:
        return "used_up", None
    if held.expires and held.expires <= now:
        return "expired", held.expires
    return "active", None
