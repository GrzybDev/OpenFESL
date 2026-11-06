from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.catalogues import Item
from openfesl.core.hero_admin import TooManyUses
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Held, Ownership, fold
from openfesl.core.store_items import requirement
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.accounts import AccountViews
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.heroes import HeroAdminViews, HeroPage, InventoryRow
from openfesl.web.pages import whole_number
from openfesl.web.sessions import checked_form, toast, toast_at
from openfesl.web.store_texts import StoreTexts

SECTION = "accounts"
# How many items a search lists at most.
MATCHES = 8
# The uses a grant of an item counted in uses adds unless the admin says otherwise.
USES = 10


@dataclass(frozen=True, slots=True)
class Tile:
    """One way the grant form offers to own an item.

    Attributes:
        key: perm, a number of days such as 7d, or uses.
        title: Its title.
        hint: What it does for the hero.
        glyph: Its glyph.
        disabled: Whether the item cannot be owned this way.
    """

    key: str
    title: str
    hint: str
    glyph: str
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class Grant:
    """What the grant form of a hero shows for what was searched and chosen so far.

    Attributes:
        query: The search, a name or an item number.
        matches: The items it found, by number with the catalogue's item where known.
        item: The number of the chosen item, if one is chosen.
        fit: Why the hero may have the chosen item, or why not.
        fits: Whether the hero may have it.
        tiles: The ways to own it.
        ownership: The chosen way.
        uses: The uses to add, for an item counted in uses.
        result: What the grant does to what the hero holds; empty until it is known.
        over: The uses the hero would hold, when that is more than the game reads.
    """

    query: str
    matches: list[tuple[int, Item | None]]
    item: int | None
    fit: str
    fits: bool
    tiles: list[Tile]
    ownership: str
    uses: int
    result: str
    over: int | None = None


def grant_form(page: HeroPage, chosen: Mapping[str, object], language: str) -> Grant:
    """Return what a hero's grant form shows for what its fields chose.

    Args:
        page: The hero's page.
        chosen: The form's fields: q, item, ownership and uses.
        language: The page's language.
    """
    game, catalogue, hero = page.game, page.catalogue, page.card.hero
    rules = game.heroes
    assert rules is not None
    texts = StoreTexts(game, language)
    query = str(chosen.get("q", "")).strip()
    typed = whole_number(query, MOST_BALANCE)
    matches: list[tuple[int, Item | None]] = []
    if typed is not None:
        matches.append((typed, catalogue.item(typed)))
    if query and catalogue.imported:
        named = [
            item
            for item in catalogue.contents.items.values()
            if item.kind not in rules.granted
            and query.casefold() in (catalogue.name(item.id) or "").casefold()
        ]
        named.sort(key=lambda item: (catalogue.name(item.id) or "").casefold())
        matches += [(item.id, item) for item in named if item.id != typed]
    if catalogue.imported:
        matches = [(number, item) for number, item in matches if item is not None]
    matches = matches[:MATCHES]
    item_id = whole_number(str(chosen.get("item", "")), MOST_BALANCE)
    item = catalogue.item(item_id) if item_id is not None else None
    if item_id is None or (catalogue.imported and (item is None or item.kind in rules.granted)):
        return Grant(query, matches, None, "", False, [], "", USES, "")
    if (item_id, item) not in matches:
        matches.insert(0, (item_id, item))
    row = next((row for row in page.items if row.item_id == item_id), None)
    held = row.held if row and row.state == "active" else None
    name = catalogue.name(item_id) or texts.text("web.admin.items.unnamed", id=item_id)
    hero_name = hero.persona.name
    unfit = requirement(game, item).unfit(hero) if item else None
    if held is None and item and item.base and unfit is None:
        # Every hero that a base item fits holds it for good without a grant.
        held = Held(PERMANENT)
    if item is None:
        fit = texts.text("web.admin.items.unchecked")
    else:
        rule = texts.requires(requirement(game, item))
        fit = texts.text(
            "web.admin.items.unfit" if unfit else "web.admin.items.fit",
            hero=hero_name,
            item=name,
            rule=rule,
        )
    # The store sells it as uses, so it is held as uses too.
    sold_in_uses = item is not None and game.store is not None and game.store.counted(item)
    counted = (held is not None and held.type == COUNTED) or sold_in_uses
    days = sorted(game.store.rentals) if game.store else []
    keys = ["perm", *(f"{count}d" for count in days), "uses"]
    picked = str(chosen.get("ownership", ""))
    if picked not in keys or (counted and picked != "uses"):
        picked = "uses" if counted else "perm"
    uses = whole_number(str(chosen.get("uses", "")), MOST_BALANCE) or USES
    now = datetime.now(UTC)
    tiles = [_tile(texts, key, held, counted, item is not None, now) for key in keys]
    gained = _ownership(picked, uses)
    after = fold(held, gained, now)
    result = _result(texts, held, gained, after)
    over = after.uses if after and (after.uses or 0) > MOST_BALANCE else None
    return Grant(query, matches, item_id, fit, unfit is None, tiles, picked, uses, result, over)


def hero_item_routes(
    sessions: AdminSessions, accounts: AccountViews, views: HeroAdminViews
) -> APIRouter:
    """Return granting a hero an item, changing a rental's end or the uses left, and revoking.

    Each needs the admin role and asks for a note; the grant form searches the game's
    catalogue, says whether the item fits the hero and what the grant adds. Each is a page of its
    own, which opens as a dialog over the hero's page where scripts run.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        accounts: Puts together what the panel shows about the accounts.
        views: Puts together what the panel shows about the heroes, and changes them.
    """
    router = APIRouter()
    path = "/admin/accounts/{account_id}/heroes/{hero_id}"

    def held(page: HeroPage, item_id: int) -> InventoryRow:
        # An item the admin may change or revoke: one in use that the hero holds by a grant.
        row = next((row for row in page.items if row.item_id == item_id), None)
        if row is None or row.entry is None or row.state != "active":
            raise HTTPException(404)
        return row

    async def render(
        request: Request,
        admin: Admin,
        page: HeroPage,
        template: str,
        status: int,
        **context: object,
    ) -> HTMLResponse:
        return await sessions.render(request, template, status, admin=admin, page=page, **context)

    def back(request: Request, account_id: str, hero_id: int, sent: str = "") -> str:
        return back_to(request, sent, path.format(account_id=account_id, hero_id=hero_id))

    @router.get(path + "/items/grant", response_class=HTMLResponse)
    async def ask_grant(request: Request, account_id: str, hero_id: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        grant = grant_form(page, request.query_params, request.state.language)
        address = request.query_params.get("back", "")
        return await render(
            request,
            admin,
            page,
            "admin.ItemGrant",
            200,
            grant=grant,
            note="",
            error="",
            back=back(request, account_id, hero_id, address),
        )

    @router.post(path + "/items/grant")
    async def grant(request: Request, account_id: str, hero_id: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        shown = grant_form(page, form, request.state.language)
        note = str(form.get("note", "")).strip()
        address = back(request, account_id, hero_id, str(form.get("back", "")))
        error = "item" if shown.item is None else "fit" if not shown.fits else ""
        error = error or ("result" if not shown.result else "") or ("more" if shown.over else "")
        error = error or ("note" if not note else "")
        if error:
            return await render(
                request,
                admin,
                page,
                "admin.ItemGrant",
                400,
                grant=shown,
                note=note,
                error=error,
                back=address,
            )
        assert shown.item is not None
        gained = _ownership(shown.ownership, shown.uses)
        hero = page.card.hero
        name = page.catalogue.name(shown.item) or str(shown.item)
        try:
            after = await views.store.grant_item(hero.persona, shown.item, gained, note, admin)
        except TooManyUses:
            return toast_at(
                request,
                address,
                "web.admin.toast.item.more",
                error=True,
                item=name,
                hero=hero.persona.name,
            )
        if after is None:
            toast(
                request, "web.admin.toast.item.held", error=True, item=name, hero=hero.persona.name
            )
        else:
            texts = StoreTexts(page.game, request.state.language)
            toast(
                request,
                "web.admin.toast.item.granted",
                item=name,
                hero=hero.persona.name,
                held=_holding(texts, after),
            )
        return RedirectResponse(address, 303)

    @router.get(path + "/items/{item_id}/{change}", response_class=HTMLResponse)
    async def ask_change(
        request: Request, account_id: str, hero_id: int, item_id: int, change: str
    ) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        if change not in ("change", "revoke"):
            raise HTTPException(404)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        row = held(page, item_id)
        if change == "change" and row.held.type not in (RENTED, COUNTED):
            raise HTTPException(404)
        return await render(
            request,
            admin,
            page,
            "admin.ItemChange",
            200,
            row=row,
            change=change,
            form={},
            back=back(request, account_id, hero_id, request.query_params.get("back", "")),
        )

    @router.post(path + "/items/{item_id}/{change}")
    async def change_item(
        request: Request, account_id: str, hero_id: int, item_id: int, change: str
    ) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        if change not in ("change", "revoke"):
            raise HTTPException(404)
        form = await checked_form(request)
        page = await views.page(accounts, account_id, hero_id, request.state.language)
        row = held(page, item_id)
        address = back(request, account_id, hero_id, str(form.get("back", "")))
        note = str(form.get("note", "")).strip()
        written = str(form.get("value", "")).strip()
        value = _value(row.held, written) if change == "change" else None
        error = "note" if not note else ""
        if change == "change" and value is None:
            error = "value"
        if error:
            fields = {"value": written, "note": note, "error": error}
            return await render(
                request,
                admin,
                page,
                "admin.ItemChange",
                400,
                row=row,
                change=change,
                form=fields,
                back=address,
            )
        persona = page.card.hero.persona
        name = page.catalogue.name(item_id) or str(item_id)
        if change == "revoke":
            done = await views.store.revoke_item(persona, item_id, note, admin)
        else:
            expires = value if isinstance(value, datetime) else None
            uses = value if isinstance(value, int) else None
            done = await views.store.change_item(persona, item_id, expires, uses, note, admin)
        key = f"web.admin.toast.item.{change}d" if done else "web.admin.toast.item.unchanged"
        return toast_at(request, address, key, error=not done, item=name, hero=persona.name)

    return router


def _ownership(key: str, uses: int) -> Ownership:
    if key == "uses":
        return Ownership(COUNTED, uses=uses)
    if key.endswith("d"):
        return Ownership(RENTED, days=int(key[:-1]))
    return Ownership(PERMANENT)


def _tile(
    texts: StoreTexts, key: str, held: Held | None, counted: bool, known: bool, now: datetime
) -> Tile:
    # The catalogue tells whether an item is counted in uses; without it every way is open.
    gained = _ownership(key, 1)
    if key == "uses":
        title = texts.text("web.admin.items.tile.uses")
        if held and held.type == COUNTED:
            return Tile(key, title, texts.count("web.admin.items.tile.left", held.uses or 0), key)
        if known and not counted:
            return Tile(key, title, texts.text("web.admin.items.tile.not_counted"), key, True)
        return Tile(key, title, texts.text("web.admin.items.tile.below"), key)
    title = (
        texts.text("web.store.price.permanent")
        if key == "perm"
        else texts.count("web.store.price.days", gained.days or 0)
    )
    glyph = "infinity" if key == "perm" else "clock"
    if counted:
        return Tile(key, title, texts.text("web.admin.items.tile.counted"), glyph, True)
    if held and held.type == PERMANENT:
        return Tile(key, title, texts.text("web.admin.items.tile.held"), glyph, True)
    if key == "perm":
        replaces = held is not None and held.expires is not None
        hint = texts.text(f"web.admin.items.tile.{'replaces' if replaces else 'kept'}")
        return Tile(key, title, hint, glyph)
    after = fold(held, gained, now)
    assert after is not None and after.expires is not None
    return Tile(
        key, title, texts.text("web.admin.items.ends", date=texts.day(after.expires)), glyph
    )


def _result(texts: StoreTexts, held: Held | None, gained: Ownership, after: Held | None) -> str:
    # What a grant does to what the hero holds; nothing for an item it holds for good.
    if after is None or (held is not None and held.type == PERMANENT):
        return ""
    if held is None:
        return texts.text("web.admin.items.new", held=_holding(texts, after))
    if after.type == COUNTED:
        old, new = texts.amount(held.uses or 0), texts.amount(after.uses or 0)
        return texts.count("web.admin.items.adds_uses", gained.uses or 0, old=old, new=new)
    if after.type == PERMANENT:
        assert held.expires is not None
        return texts.text("web.admin.items.becomes_permanent", date=texts.day(held.expires))
    assert held.expires is not None and after.expires is not None
    old, new = texts.day(held.expires), texts.day(after.expires)
    return texts.count("web.admin.items.adds_days", gained.days or 0, old=old, new=new)


def _holding(texts: StoreTexts, held: Held) -> str:
    # How a hero holds an item, in the words of the inventory.
    if held.type == COUNTED:
        return texts.count("web.store.tile.uses", held.uses or 0)
    if held.expires:
        return texts.text("web.admin.items.ends", date=texts.day(held.expires))
    return texts.text("web.store.price.permanent")


def _value(held: Held, written: str) -> datetime | int | None:
    # A rental's new end, a day from which it lasts to the day's end in UTC, or uses left.
    if held.type == COUNTED:
        return whole_number(written, MOST_BALANCE, 0)
    try:
        day = datetime.fromisoformat(written).date()
    except ValueError:
        return None
    end = datetime.combine(day, time(23, 59), UTC)
    return end if end > datetime.now(UTC) else None
