from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.claw import Claw, ClawRefusal, ClawRefused, Grabbed
from openfesl.core.claw_rules import ClawRules
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero
from openfesl.core.inventory import COUNTED, PERMANENT, Ownership
from openfesl.core.texts import localize, translate
from openfesl.core.wallets import Wallets
from openfesl.web.account.session import open_page, signed_in
from openfesl.web.catalogue_files import icon_url
from openfesl.web.pages import Pages
from openfesl.web.sessions import (
    SentForms,
    form_matches,
    form_token,
    inside,
    purchase_matches,
    toast,
)
from openfesl.web.site import CLAW, CLAW_CLOSED, SiteBar, site_bar
from openfesl.web.store_texts import StoreTexts

# What the last grab gave, which the next page of the Claw shows once.
GRABBED = "claw_grabbed"
# How many prizes each of the machine's belts holds at least, so that it shows no gap.
BELT = 6


@dataclass(frozen=True, slots=True)
class PrizeLine:
    """A prize as the Claw's belts, its chances and its feed show it.

    Attributes:
        item_id: The item's number.
        name: The item's name.
        held: For how long or how many uses the hero gets it.
        icon: The address of its icon, where the page shows the game's art.
        glyph: The glyph of its kind, which stands in for the icon.
        kind: The kind's name, said beside the glyph.
        rare: Whether it is rare.
        chance: Its chance at a grab, as the page's language writes it.
    """

    item_id: int
    name: str
    held: str
    icon: str | None
    glyph: str
    kind: str
    rare: bool
    chance: str = ""


@dataclass(frozen=True, slots=True)
class TierLine:
    """A tier of a hero's prizes in the chances list.

    Attributes:
        name: Its name.
        share: Its share of the grabs, as the page's language writes it.
        prizes: Its prizes that the hero may win.
    """

    name: str
    share: str
    prizes: list[PrizeLine]


@dataclass(frozen=True, slots=True)
class HeroPrizes:
    """One of the account's heroes with the prizes it may win.

    Attributes:
        hero: The hero.
        belts: Its prizes over the machine's three belts.
        tiers: Its chances by tier.
        owned: The names of the prizes left out as it owns them for good.
    """

    hero: Hero
    belts: list[list[PrizeLine]]
    tiers: list[TierLine]
    owned: list[str]


@dataclass(frozen=True, slots=True)
class FeedLine:
    """A rare prize of the feed.

    Attributes:
        hero_id: The persona of the hero who won it.
        hero: The hero's name.
        prize: The prize.
        at: When it was won.
        own: Whether the hero is one of the viewing account's.
        new: Whether it is the grab whose prize the page shows.
        words: The words of its line around the hero's name and the prize's, in order.
    """

    hero_id: int
    hero: str
    prize: PrizeLine
    at: datetime
    own: bool
    new: bool
    words: tuple[str, str, str]


class ClawPages:
    """The Daily Claw of each game that has one: its page, its grabs and closing its overlay."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        claw: Claw,
        sent: SentForms,
        catalogues: Catalogues,
        wallets: Wallets,
    ) -> None:
        """Prepare the Claw of each game that lists its page.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            claw: Runs the Claw.
            sent: Lets each form of the Claw grab once.
            catalogues: Name and draw the prizes, once imported.
            wallets: Tell what the account has to pay grabs with, opening nothing.
        """
        self._pages = pages
        self._games = games
        self._claw = claw
        self._sent = sent
        self._catalogues = catalogues
        self._wallets = wallets

    def routes(self) -> APIRouter:
        """Return each game's Claw page, its grab form and the form that closes its overlay."""
        router = APIRouter()
        for game in self._games.with_page("claw"):
            base = f"/{game.id}{CLAW}"
            router.add_api_route(base, self._page(game), response_class=HTMLResponse)
            router.add_api_route(base, self._grab(game), methods=["POST"])
            router.add_api_route(base + "/close", self._close(game), methods=["POST"])
            # The website's own address of the Claw in 2012.
            router.add_api_route(f"/{game.id}/spinner", self._moved(game))
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_claw(request: Request) -> Response:
            language = request.state.language
            rules, running = await self._claw.rules(game), await self._claw.running(game)
            catalogue = await self._catalogues.of(game, language)
            texts = StoreTexts(game, language)
            art = self._pages.shows_art(request, game)
            player, bar = signed_in(request), await site_bar(request)
            grabbed = request.session.pop(GRABBED, None) if player else None
            context: dict[str, Any] = {}
            if running:
                feed = await self._feed(game, running, texts, catalogue, art, bar, grabbed)
                context = {"feed": feed}
            if running and player and bar and bar.roster is not None:
                heroes = [hero for hero in bar.roster.heroes if hero.playable]
                [funds] = await self._wallets.of_account(
                    game, player.account_id, [running.paid.currency]
                )
                default = bar.roster.default_id
                day = await self._claw.day(game, running, player.account_id)
                context |= {
                    "day": day,
                    "heroes": [await self._hero(game, texts, catalogue, h, art) for h in heroes],
                    "picked": default if any(h.persona.id == default for h in heroes) else None,
                    "funds": funds,
                    "grabbed": _card(texts, catalogue, grabbed, art) if grabbed else None,
                    "left": texts.countdown(day.next_free),
                }
            return self._pages.render(
                request,
                "claw.Claw",
                game=game,
                rules=rules,
                running=running,
                imported=catalogue.imported,
                name=(catalogue.text(rules.name) if rules else None)
                or translate("web.claw.name", language),
                csrf=form_token(request) if player else "",
                **context,
            )

        return show_claw

    def _grab(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def grab(request: Request) -> Response:
            form = await request.form()
            page = localize(f"/{game.id}{CLAW}", request.state.language)
            player = signed_in(request)
            if player is None:
                return open_page(request, "/sign-in", page)
            if not purchase_matches(request, form):
                toast(request, "web.claw.refused.form", error=True)
                return RedirectResponse(page, 303)
            chosen = str(form.get("hero", ""))
            hero_id = int(chosen) if chosen.isascii() and chosen.isdecimal() else 0
            texts = StoreTexts(game, request.state.language)
            # A paid grab is charged only at the price and in the currency that the page showed.
            rules = await self._claw.running(game)
            charged = (str(rules.paid.price), rules.paid.currency) if rules else None
            shown = (str(form.get("price", "")), str(form.get("currency", "")))
            if "paid" in form and rules and rules.paid.enabled and shown != charged:
                price = texts.priced(rules.paid.price, rules.paid.currency)
                toast(request, "web.claw.refused.price", error=True, price=price)
                return RedirectResponse(page, 303)
            try:
                async with self._sent.once(form) as grabbed:
                    if grabbed:
                        toast(request, "web.claw.done.again", error=False)
                        return RedirectResponse(page, 303)
                    done = await self._claw.grab(game, player.account_id, hero_id, "paid" in form)
            except ClawRefused as refused:
                values = await self._why(texts, game, player.account_id, refused.refusal)
                toast(request, f"web.claw.refused.{refused.refusal}", error=True, **values)
                return RedirectResponse(page, 303)
            request.session[GRABBED] = _grabbed(done)
            return RedirectResponse(page, 303)

        return grab

    def _close(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def close_claw(request: Request) -> Response:
            form = await request.form()
            language = request.state.language
            places = (f"/{game.id}", f"/{language}/{game.id}")
            back = inside(str(form.get("next", "")), places) or localize(f"/{game.id}", language)
            bar = await site_bar(request)
            day = bar.claw.day if bar and bar.claw else None
            if day and form_matches(request, form):
                request.session[CLAW_CLOSED] = day.isoformat()
            return RedirectResponse(back, 303)

        return close_claw

    def _moved(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def moved(request: Request) -> Response:
            return RedirectResponse(localize(f"/{game.id}{CLAW}", request.state.language), 301)

        return moved

    async def _why(
        self, texts: StoreTexts, game: Game, account_id: int, refusal: ClawRefusal
    ) -> dict[str, str]:
        # What a refusal's text names: who took the free grab and when, or how short the
        # account is of the price.
        rules = await self._claw.rules(game)
        if refusal is ClawRefusal.USED and rules:
            used = (await self._claw.day(game, rules, account_id)).used
            if used:
                time = texts.text("web.store.utc", time=f"{used[0].at.astimezone(UTC):%H:%M}")
                return {"time": time, "hero": used[0].hero}
        if refusal is ClawRefusal.FUNDS and rules:
            paid = rules.paid
            [have] = await self._wallets.of_account(game, account_id, [paid.currency])
            return {
                "have": texts.priced(have, paid.currency),
                "price": texts.priced(paid.price, paid.currency),
                "short": texts.priced(paid.price - have, paid.currency),
            }
        return {}

    async def _hero(
        self, game: Game, texts: StoreTexts, catalogue: Catalogue, hero: Hero, art: bool
    ) -> HeroPrizes:
        # A hero's prizes over three belts and by tier, with their chances.
        prizes, owned = await self._claw.prizes(game, hero)
        lines = [
            replace(
                _line(texts, catalogue, p.product.item_id, p.product.ownership, art, p.rare),
                chance=texts.percent(float(p.chance), "#,##0.##%"),
            )
            for p in prizes
        ]
        tiers: dict[int, tuple[Fraction, list[PrizeLine]]] = {}
        for prize, line in zip(prizes, lines, strict=True):
            share, found = tiers.get(prize.tier, (Fraction(0), []))
            tiers[prize.tier] = (share + prize.chance, [*found, line])
        rules = game.claw
        assert rules is not None
        tier_lines = [
            TierLine(
                texts.text(f"games.{game.id}.claw.tier.{rules.tiers[tier].name}")
                if tier < len(rules.tiers) and rules.tiers[tier].name
                else texts.text("web.admin.boxes.tier", number=tier + 1),
                texts.percent(float(share), "#,##0.##%"),
                found,
            )
            for tier, (share, found) in sorted(tiers.items())
        ]
        belts = [lines[row::3] or lines for row in range(3)]
        belts = [belt * -(-BELT // len(belt)) if belt else [] for belt in belts]
        names = [texts.item_name(catalogue, product.item_id) for product in owned]
        return HeroPrizes(hero, belts, tier_lines, names)

    async def _feed(
        self,
        game: Game,
        rules: ClawRules,
        texts: StoreTexts,
        catalogue: Catalogue,
        art: bool,
        bar: SiteBar | None,
        grabbed: dict[str, Any] | None,
    ) -> list[FeedLine]:
        # The latest rare prizes; the grab just made is marked on the first line that is its.
        roster = bar.roster if bar else None
        mine = {hero.persona.id for hero in roster.heroes} if roster else set()
        found, marked = [], False
        # The line's words, split where the names go, so that the hero's name can be a link.
        row = texts.text("web.claw.feed.row", hero="\0", item="\1")
        before, _, rest = row.partition("\0")
        between, _, after = rest.partition("\1")
        for won in await self._claw.feed(game, rules):
            line = _line(texts, catalogue, won.item_id, won.ownership, art, True)
            mark = (grabbed["hero_id"], grabbed["item"]) if grabbed else None
            new = not marked and mark == (won.hero_id, won.item_id)
            marked = marked or new
            own = won.hero_id in mine
            found.append(
                FeedLine(won.hero_id, won.hero, line, won.at, own, new, (before, between, after))
            )
        return found


def _line(
    texts: StoreTexts,
    catalogue: Catalogue,
    item_id: int,
    ownership: Ownership,
    art: bool,
    rare: bool,
) -> PrizeLine:
    # A prize with its icon, or the glyph and name of its kind where the art is not shown.
    game = texts.game
    item = catalogue.item(item_id)
    shelf = game.store.in_game if game.store else None
    category = (
        next((c for c in shelf.categories if item and item.kind in c.kinds), None)
        if shelf
        else None
    )
    return PrizeLine(
        item_id,
        texts.item_name(catalogue, item_id),
        texts.ownership(ownership),
        icon_url(game.id, catalogue, item_id, art),
        category.glyph if category else "items",
        texts.text(f"web.claw.kind.{category.key}") if category else "",
        rare,
    )


def _grabbed(done: Grabbed) -> dict[str, Any]:
    # What the next page shows of a grab, as the session keeps it: the prize and what the hero
    # held of it before and holds now.
    after = done.after
    return {
        "hero": done.hero.persona.name,
        "hero_id": done.hero.persona.id,
        "item": done.product.item_id,
        "ownership": done.product.ownership.written,
        "rare": done.rare,
        "before": done.before.uses if done.before else None,
        "held": after.type,
        "uses": after.uses,
        "expires": after.expires.isoformat() if after.expires else None,
    }


def _card(
    texts: StoreTexts, catalogue: Catalogue, grabbed: dict[str, Any], art: bool
) -> dict[str, Any]:
    # The prize card of a grab as the session kept it, in the page's language.
    ownership = Ownership.read(grabbed["ownership"])
    prize = _line(texts, catalogue, grabbed["item"], ownership, art, grabbed["rare"])
    hero, held, uses, before = grabbed["hero"], grabbed["held"], grabbed["uses"], grabbed["before"]
    if held == PERMANENT:
        line = texts.text("web.claw.prize.permanent", hero=hero)
    elif held == COUNTED and before:
        added = {"before": texts.amount(before), "after": texts.amount(uses or 0)}
        line = texts.text("web.claw.prize.added", hero=hero, **added)
    elif held == COUNTED:
        line = texts.text(
            "web.claw.prize.uses", hero=hero, uses=texts.count("web.store.price.uses", uses or 0)
        )
    else:
        ends = datetime.fromisoformat(grabbed["expires"])
        days = texts.count("web.store.price.days", texts.days_left(ends))
        line = texts.text("web.claw.prize.days", hero=hero, days=days)
    login = texts.text("web.claw.prize.login", game=texts.game.name, hero=hero)
    return {**grabbed, "prize": prize, "line": f"{line} {login}"}
