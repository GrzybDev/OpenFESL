from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.entitlements import ACTIVE, EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.gift_codes import CodeRefusal, CodeRefused, GiftCodes, Look, Redemption
from openfesl.core.hero_items import held, holding
from openfesl.core.heroes import Hero
from openfesl.core.inventory import COUNTED, PERMANENT, Held, Ownership, fold
from openfesl.core.reward_rules import Gift
from openfesl.core.store_items import Requirement, Unfit, requirement
from openfesl.core.texts import localize
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.account.session import open_page, signed_in
from openfesl.web.catalogue_files import icon_url
from openfesl.web.pages import Pages, listed
from openfesl.web.sessions import form_matches, form_token, toast
from openfesl.web.site import site_bar
from openfesl.web.store_texts import StoreTexts

# What a code just redeemed gave, which the next page of the redeem form shows once.
DONE = "redeem_done"


@dataclass(frozen=True, slots=True)
class GiftRow:
    """One thing a code gives, as a row of its contents.

    Attributes:
        name: An item's name, or that of the Funds or the keys.
        whom: Which heroes may have an item, or that it goes to the account.
        what: How an item is given, or how many Funds or keys.
        icon: The address of an item's icon, where the page shows the game's art.
        glyph: The glyph of the Funds or the keys, or of an item without an icon.
        missing: Whether the imported catalogue lacks the item, which is given by its number.
    """

    name: str
    whom: str
    what: str
    icon: str | None = None
    glyph: str = ""
    missing: bool = False


@dataclass(frozen=True, slots=True)
class HeroTile:
    """One of the account's heroes as the redeem form offers it for a code's items.

    Attributes:
        hero: The hero.
        lines: What the code changes of each item the hero holds, each with its glyph.
        unfit: Why the hero may not have the items; empty when it may.
        checked: Whether the form picks it at first: the default hero, or else the first.
    """

    hero: Hero
    lines: list[tuple[str, str]]
    unfit: str
    checked: bool


class RedeemPages:
    """The page of a game's site that redeems codes: what a code gives, and redeeming it."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        codes: GiftCodes,
        catalogues: Catalogues,
        entitlements: EntitlementStore,
    ) -> None:
        """Prepare the redeem page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            codes: Looks codes up and redeems them.
            catalogues: Name and draw the items that codes give, once imported.
            entitlements: Hold what the heroes own, which says what a code changes.
        """
        self._pages = pages
        self._games = games
        self._codes = codes
        self._catalogues = catalogues
        self._entitlements = entitlements

    def routes(self) -> APIRouter:
        """Return each game's redeem page and its form."""
        router = APIRouter()
        for game in self._games.with_page("redeem"):
            assert game.site is not None
            path = f"/{game.id}{game.site.path('redeem')}"
            router.add_api_route(path, self._page(game), response_class=HTMLResponse)
            router.add_api_route(path, self._redeem(game), methods=["POST"])
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_redeem(request: Request) -> Response:
            player = signed_in(request)
            typed = request.query_params.get("key", "")
            assert game.codes is not None
            if player is None or not typed:
                done = request.session.pop(DONE, None) if player else None
                shown = game.codes.read(typed) or typed.strip()
                return await self._render(request, game, shown, None, None, done=done)
            look = await self._codes.look(game, player.account_id, typed)
            # The entry form's button names itself, so that a code arriving without it came
            # with a link.
            linked = request.query_params.get("from") != "form"
            return await self._render(request, game, look.code, look, look.refusal, linked)

        return show_redeem

    def _redeem(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def redeem(request: Request) -> Response:
            form = await request.form()
            typed = str(form.get("key", ""))
            base = localize(f"/{game.id}{_path(game)}", request.state.language)
            # As the entry form sends it, so that the page does not take the code for a link's.
            again = f"{base}?{urlencode({'key': typed, 'from': 'form'})}" if typed else base
            player = signed_in(request)
            if player is None:
                return open_page(request, "/sign-in", again)
            if not form_matches(request, form):
                toast(request, "web.redeem.refused.form", error=True)
                return RedirectResponse(again, 303)
            chosen = str(form.get("hero", ""))
            hero_id = int(chosen) if chosen.isascii() and chosen.isdecimal() else None
            try:
                done = await self._codes.redeem(game, player.account_id, typed, hero_id)
            except CodeRefused as refused:
                # As it stands now, with what was redeemed meanwhile; a hero chosen that may not
                # have the items, as on a page left open, shows the heroes again.
                look = await self._codes.look(game, player.account_id, typed)
                refusal: CodeRefusal | None = look.refusal or refused.refusal
                if refusal is CodeRefusal.NO_FIT and look.fitting:
                    refusal = None
                return await self._render(
                    request, game, look.code, look, refusal, status=400, raced=True
                )
            texts, catalogue = await self._words(request, game)
            request.session[DONE] = _done(texts, catalogue, done)
            given = [texts.priced(done.funds[0], _currency(game))] if done.funds else []
            if done.keys:
                given.append(texts.count("web.admin.keys.gift.keys", done.keys[0]))
            key = "web.redeem.done.hero" if done.hero else "web.redeem.done.account"
            if given:
                key += ".wallet"
            toast(
                request,
                key,
                code=done.code,
                hero=done.hero or "",
                given=listed(given, texts.language),
                funds=str(done.funds[0] if done.funds else 0),
            )
            return RedirectResponse(base, 303)

        return redeem

    async def _render(
        self,
        request: Request,
        game: Game,
        code: str,
        look: Look | None,
        refusal: CodeRefusal | None,
        linked: bool = False,
        done: dict[str, object] | None = None,
        status: int = 200,
        raced: bool = False,
    ) -> Response:
        texts, catalogue = await self._words(request, game)
        entry = look.entry if look else None
        rows = (
            _rows(texts, catalogue, entry.gift, self._pages.shows_art(request, game))
            if entry
            else []
        )
        bar = await site_bar(request)
        default = bar.roster.default_id if bar and bar.roster else None
        tiles = []
        if look and entry and entry.gift.items and refusal is None:
            tiles = await self._tiles(texts, catalogue, look, default)
        return self._pages.render(
            request,
            "store.Redeem",
            status,
            game=game,
            code=code,
            look=look,
            entry=entry,
            refusal=refusal,
            said=_refusal(texts, catalogue, look, refusal, raced) if refusal else ("", ""),
            rows=rows,
            tiles=tiles,
            linked=linked,
            done=done,
            free=bar.roster.free if bar and bar.roster else 0,
            csrf=form_token(request),
        )

    async def _tiles(
        self, texts: StoreTexts, catalogue: Catalogue, look: Look, default: int | None
    ) -> list[HeroTile]:
        # Each hero with what the code changes of each item it holds, or why it may not have
        # them; the default hero is picked where it may, else the first that may.
        assert look.entry is not None
        rules = texts.game.heroes
        assert rules is not None
        picked = default if default in look.fitting else next(iter(look.fitting), None)
        now = datetime.now(UTC)
        tiles = []
        for hero in look.heroes:
            if hero.persona.id not in look.fitting:
                tiles.append(HeroTile(hero, [], _unfit(texts, catalogue, look, hero), False))
                continue
            kept = [
                entry
                for entry in await self._entitlements.of_persona(hero.persona.id)
                if entry.status == ACTIVE and (entry.expires is None or entry.expires > now)
            ]
            holds = held(rules, hero.faction, hero.hero_class, kept)
            lines = []
            for item_id, ownership in look.entry.gift.items:
                before = holding(item_id, kept) if item_id in holds else None
                lines.append(_change(texts, catalogue, look, item_id, ownership, before, now))
            tiles.append(HeroTile(hero, lines, "", hero.persona.id == picked))
        return tiles

    async def _words(self, request: Request, game: Game) -> tuple[StoreTexts, Catalogue]:
        language = request.state.language
        return StoreTexts(game, language, datetime.now(UTC)), await self._catalogues.of(
            game, language
        )


def _path(game: Game) -> str:
    assert game.site is not None
    return game.site.path("redeem")


def _currency(game: Game) -> str:
    assert game.rewards is not None
    return game.rewards.currency


def _name(texts: StoreTexts, catalogue: Catalogue, item_id: int) -> str:
    return catalogue.name(item_id) or texts.text("web.redeem.item", id=item_id)


def _rows(texts: StoreTexts, catalogue: Catalogue, gift: Gift, art: bool) -> list[GiftRow]:
    # What a code gives: its items with whom they fit and how each is given, then the Funds and
    # keys for the account.
    game = texts.game
    rows = []
    for item_id, ownership in gift.items:
        item = catalogue.item(item_id)
        whom = texts.requires(requirement(game, item)) if item else ""
        rows.append(
            GiftRow(
                _name(texts, catalogue, item_id),
                whom,
                texts.ownership(ownership),
                icon_url(game.id, catalogue, item_id, art),
                "doc",
                missing=catalogue.imported and item is None,
            )
        )
    account = texts.text("web.redeem.to_account")
    if gift.funds:
        name = texts.currency(_currency(game))
        rows.append(GiftRow(name, account, f"+{texts.amount(gift.funds)}", glyph="coin"))
    if gift.keys:
        name = texts.text("web.redeem.keys")
        rows.append(GiftRow(name, account, f"+{texts.amount(gift.keys)}", glyph="key"))
    return rows


def _change(
    texts: StoreTexts,
    catalogue: Catalogue,
    look: Look,
    item_id: int,
    gained: Ownership,
    before: Held | None,
    now: datetime,
) -> tuple[str, str]:
    # What a code changes of an item a hero holds, with the line's glyph: new, days or uses
    # added, made permanent, or the fallback in place of an item owned for good.
    item = _name(texts, catalogue, item_id)
    what = texts.ownership(gained, inline=True)
    after = fold(before, gained, now)
    if before is None:
        return "plus", texts.text("web.redeem.change.new", item=item, what=what)
    if after is None:
        # Codes need rewards, whose fallback a hero gets for an item it owns for good.
        assert look.fallback is not None
        fallback, owned = look.fallback
        instead = texts.text(
            "web.deals.card.item",
            item=_name(texts, catalogue, fallback),
            ownership=texts.ownership(owned, inline=True),
        )
        return "tick", texts.text("web.redeem.change.fallback", item=item, fallback=instead)
    if after.type == COUNTED:
        total = texts.count("web.store.price.uses", after.uses or 0)
        return "tick", texts.text("web.redeem.change.uses", item=item, what=what, total=total)
    if after.type == PERMANENT:
        return "tick", texts.text("web.redeem.change.permanent", item=item)
    assert before.expires is not None and after.expires is not None
    left = texts.count("web.store.price.days", texts.days_left(before.expires))
    total = texts.count("web.store.price.days", texts.days_left(after.expires))
    return "tick", texts.text(
        "web.redeem.change.days", item=item, what=what, left=left, total=total
    )


def _unfit(texts: StoreTexts, catalogue: Catalogue, look: Look, hero: Hero) -> str:
    # Why a hero may not have a code's items, by the first item it may not have.
    assert look.entry is not None
    game = texts.game
    name = hero.persona.name
    for item_id, _ in look.entry.gift.items:
        item = catalogue.item(item_id)
        why = requirement(game, item).unfit(hero) if item else None
        if item is None or why is None:
            continue
        found = requirement(game, item)
        values = {"item": _name(texts, catalogue, item_id), "hero": name}
        if why is Unfit.FACTION:
            mine = texts.factions(frozenset({hero.faction or ""}))
            return texts.text(
                "web.redeem.unfit.faction",
                faction=texts.factions(found.factions),
                mine=mine,
                **values,
            )
        if why is Unfit.CLASS:
            mine = texts.classes(frozenset({hero.hero_class or ""}))
            return texts.text(
                "web.redeem.unfit.class", classes=texts.classes(found.classes), mine=mine, **values
            )
        return texts.text("web.redeem.unfit.level", level=found.level, mine=hero.level, **values)
    return texts.text("web.redeem.unfit.disabled", hero=name)


def _refusal(
    texts: StoreTexts,
    catalogue: Catalogue,
    look: Look | None,
    refusal: CodeRefusal,
    raced: bool,
) -> tuple[str, str]:
    # Why a code gives the account nothing, and what follows from it, such as until when it
    # stays usable for the account.
    entry = look.entry if look else None
    until = (
        texts.text("web.redeem.stays.until", end=texts.moment(entry.expires))
        if entry and entry.expires
        else texts.text("web.redeem.stays")
    )
    if refusal is CodeRefusal.EXPIRED and entry and entry.expires:
        return texts.text("web.redeem.refused.expired", date=texts.moment(entry.expires)), ""
    if refusal is CodeRefusal.REDEEMED and look and look.earlier:
        earlier = look.earlier
        key = "web.redeem.refused.raced" if raced else "web.redeem.refused.redeemed"
        if earlier.hero is None:
            key += ".account"
        return texts.text(key, date=texts.day(earlier.at), hero=earlier.hero or ""), ""
    if refusal is CodeRefusal.NO_HERO:
        return texts.text("web.redeem.refused.no_hero"), until
    if refusal is CodeRefusal.NO_FIT and entry and look:
        needed = _needed(texts, catalogue, entry.gift)
        rule = texts.requires(needed)
        # The hero of the faction and class that lacks only levels, the highest of them.
        closest = [hero for hero in look.heroes if needed.unfit(hero) is Unfit.LEVEL]
        if not closest:
            return texts.text("web.redeem.refused.no_fit", rule=rule), until
        best = max(closest, key=lambda hero: hero.level)
        said = texts.text(
            "web.redeem.refused.no_fit.level", rule=rule, hero=best.persona.name, level=best.level
        )
        return said, until
    if refusal is CodeRefusal.BALANCE and entry:
        currency = _currency(texts.game)
        amount = texts.priced(entry.gift.funds, currency)
        most = texts.priced(MOST_BALANCE, currency)
        return texts.text("web.redeem.refused.balance", amount=amount, most=most), ""
    return texts.text(f"web.redeem.refused.{refusal.value}"), ""


def _needed(texts: StoreTexts, catalogue: Catalogue, gift: Gift) -> Requirement:
    # Which heroes may have every item of a gift: the factions and classes all of them allow,
    # from the highest level any needs.
    found = [
        requirement(texts.game, item)
        for item_id, _ in gift.items
        if (item := catalogue.item(item_id))
    ]
    factions = [rule.factions for rule in found if rule.factions]
    classes = [rule.classes for rule in found if rule.classes]
    return Requirement(
        frozenset.intersection(*factions) if factions else frozenset(),
        frozenset.intersection(*classes) if classes else frozenset(),
        max((rule.level for rule in found), default=1),
    )


def _done(texts: StoreTexts, catalogue: Catalogue, done: Redemption) -> dict[str, object]:
    # What a redemption gave, a row each, as the next page shows it.
    hero = done.hero or ""
    rows = []
    for given in done.items:
        item = _name(texts, catalogue, given.item_id)
        what = texts.ownership(given.ownership, inline=True)
        if given.fallback:
            text = texts.text("web.redeem.done.fallback", item=item, what=what)
        elif left := texts.left(given.held):
            text = texts.text("web.redeem.done.added", item=item, what=what, left=left)
        else:
            text = texts.text("web.redeem.done.permanent", item=item)
        rows.append([hero, text])
    account = texts.text("web.redeem.done.account_row")
    if done.funds:
        amount, total = done.funds
        currency = _currency(texts.game)
        text = texts.text(
            "web.redeem.done.funds",
            amount=texts.priced(amount, currency),
            total=texts.priced(total, currency),
        )
        rows.append([account, text])
    if done.keys:
        count, total = done.keys
        added = texts.count("web.admin.keys.gift.keys", count)
        rows.append(
            [account, texts.text("web.redeem.done.keys", added=added, total=texts.amount(total))]
        )
    return {"code": done.code, "hero": hero, "rows": rows}
