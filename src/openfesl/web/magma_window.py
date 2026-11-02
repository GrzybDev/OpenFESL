import hashlib
import hmac
import logging
import re
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from markupsafe import Markup

from openfesl.core.catalogues import Catalogues
from openfesl.core.game_art import GameArt
from openfesl.core.game_pages import SiteWallet
from openfesl.core.games import Game
from openfesl.core.magma import Magma, MagmaError
from openfesl.core.personas import Persona
from openfesl.core.purchases import ALREADY_OWNED, INSUFFICIENT_FUNDS, PurchaseRefused, Purchases
from openfesl.core.store_window import PricePoint, Shelf, StoreWindow, StoreWindows, Ware
from openfesl.core.texts import translate
from openfesl.web.catalogue_files import icon_url
from openfesl.web.in_game import digest, inline, language_of
from openfesl.web.magma import SessionId
from openfesl.web.pages import listed, render_alone
from openfesl.web.security import HEADERS

logger = logging.getLogger(__name__)

STORE = "/{segment}/game/store"
# The game replaces a page whose title holds this word with its own offline page (BFHeroes.exe
# 1.89 0x009fd3b0), so the store answers every failure with a page of that title.
OFFLINE = "offline"
# The game names its language at the start of the store's address, such as en or br.
_SEGMENT = re.compile(r"[a-z]{2}(?:-[A-Za-z]{2})?")
# The query the game opens the store with names the hero by personaId, and what it has equipped
# by eqp: item numbers joined by +, a consumable's with _ and its uses left in the round.
_EQUIPPED = re.compile(r"(?P<item>[0-9]{1,9})(?:_(?P<uses>[0-9]{1,9}))?")
_MOST_EQUIPPED = 64
# Why a purchase was refused, as the item's page words it.
CHANGED = "changed"
OWNED = "owned"
EXPIRED = "expired"
_REFUSALS = (CHANGED, OWNED, EXPIRED)

# The store's stylesheets by part, which its pages inline as one style in this order.
_STYLES = (
    "store-window.css",
    "store-lists.css",
    "store-item.css",
    "store-overlays.css",
    "store-need.css",
)


class _Closed(Exception):
    """Keeps the store closed to a request: no hero of the session, or none that may buy."""


@dataclass(frozen=True, slots=True)
class Kit:
    """What a game's in-game pages carry inline, and the policy that allows just that.

    Attributes:
        game: The game.
        styles: The stylesheet, with the colours of the game's coins and factions.
        script: The script.
        wallets: The wallets the hero's bar shows.
        coins: The coins of the currencies, by code.
        headers: The pages' headers, with their content policy.
    """

    game: Game
    styles: Markup
    script: Markup
    wallets: list[SiteWallet]
    coins: Mapping[str, SiteWallet]
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Visit:
    """The store as the game opened it for a hero: its address, the hero's store and its forms.

    Attributes:
        segment: The language the game names in the address, such as en.
        window: The hero's store.
        persona: The hero.
        token: What the store's forms send back, from the game's session.
        equipped: The game's list of what the hero has equipped, as its query sent it.
        icons: The addresses of the items' icons as PNG pictures, which the game's browser
            decodes, by item number; an item without one shown keeps its category's glyph.
    """

    segment: str
    window: StoreWindow
    persona: Persona
    token: str
    equipped: str
    icons: Mapping[int, str]

    def path(self, *parts: str | int) -> str:
        """Return the path of a page of the store, without its query.

        Args:
            parts: The path below the store, such as a category and an item.
        """
        return "/".join([f"/{self.segment}/game/store", *map(str, parts)])

    def link(self, *parts: str | int, **values: str | int) -> str:
        """Return the address of a page of the store, keeping what the game's query said.

        Args:
            parts: The path below the store, such as a category and an item.
            values: More of the query, such as the price point picked.
        """
        kept: dict[str, str | int] = {"personaId": self.persona.id}
        if self.equipped:
            kept["eqp"] = self.equipped
        return f"{self.path(*parts)}?{urlencode(kept | values)}"


def window_router(
    game: Game,
    magma: Magma,
    windows: StoreWindows,
    purchases: Purchases,
    catalogues: Catalogues,
    art: GameArt,
) -> APIRouter:
    """Return the store that a game opens in its own browser during a match, on its Magma host.

    The game names the hero in the query and the session in its magma cookie; the hero must be
    the session's account's own. Buying goes through the store's purchases, and the page then
    tells the game through its script bridge, so that the game loads the item. The items show
    their imported icons where the game's art is shown to the game.

    Args:
        game: The game, whose store data names the in-game store's categories.
        magma: Answers about who asks.
        windows: Opens a hero's store.
        purchases: Sells the store's products.
        catalogues: Know the items' icons.
        art: Says to whom the game's art is shown.
    """
    router = APIRouter()
    if game.store is None or game.store.in_game is None:
        return router
    kit = _kit(game)

    async def visit(request: Request, segment: str, session_id: str | None) -> Visit:
        query = request.query_params
        persona_id = _number(query.get("personaId"))
        if not persona_id or not _SEGMENT.fullmatch(segment):
            raise _Closed
        session = magma.session(session_id)
        persona = await magma.persona(session_id, game, persona_id, own=True)
        entries = _entries(query.get("eqp", ""))
        items = [int(entry["item"]) for entry in entries]
        uses = {int(entry["item"]): int(entry["uses"]) for entry in entries if entry["uses"]}
        window = await windows.open(game, persona, game.language_of(segment), items, uses)
        if window is None:
            raise _Closed
        token = hmac.new(session.token.encode(), b"in-game store", hashlib.sha256).hexdigest()
        # The game counts as signed in through the session that opened the store.
        catalogue = await catalogues.of(game, game.language_of(segment))
        shown = art.shown(game, signed_in=True)
        icons = {
            ware.item.id: src
            for shelf in window.shelves
            for ware in shelf.wares
            if (src := icon_url(game.id, catalogue, ware.item.id, shown, as_png=True))
        }
        equipped = " ".join(entry[0] for entry in entries)
        return Visit(segment, window, persona, token, equipped, icons)

    def page(segment: str, template: str, **context: Any) -> Response:
        language = language_of(game, segment)
        return render_alone(template, language, headers=kit.headers, kit=kit, **context)

    async def guarded(segment: str, answer: Awaitable[Response]) -> Response:
        # The game shows its own offline page in place of any failure's, which the page's title
        # asks it to, and an unknown or ended session ends up here too.
        try:
            return await answer
        except MagmaError, _Closed:
            pass
        except Exception:
            logger.exception("The in-game store of %s failed", game.name)
        return page(segment, "ingame.Offline", title=OFFLINE)

    @router.get(STORE)
    async def categories(request: Request, segment: str, session_id: SessionId = None) -> Response:
        async def answer() -> Response:
            found = await visit(request, segment, session_id)
            language = language_of(game, segment)
            names = [
                translate(f"games.{game.id}.currency.{wallet.currency}", language)
                for wallet in kit.wallets
            ]
            currencies = listed(names, language)
            return page(segment, "ingame.Store", visit=found, currencies=currencies)

        return await guarded(segment, answer())

    @router.get(STORE + "/{category}")
    async def category(
        request: Request, segment: str, category: str, session_id: SessionId = None
    ) -> Response:
        async def answer() -> Response:
            found = await visit(request, segment, session_id)
            shelf = found.window.shelf(category)
            if shelf is None:
                return RedirectResponse(found.link(), 303)
            highlight = request.query_params.get("hl", "")
            return page(segment, "ingame.Category", visit=found, shelf=shelf, highlight=highlight)

        return await guarded(segment, answer())

    @router.get(STORE + "/{category}/{item}")
    async def item(
        request: Request, segment: str, category: str, item: str, session_id: SessionId = None
    ) -> Response:
        async def answer() -> Response:
            found = await visit(request, segment, session_id)
            shelf, ware = _ware(found.window, category, item)
            if shelf is None or ware is None:
                return RedirectResponse(found.link(*([category] if shelf else [])), 303)
            query = request.query_params
            bought = ware.point(_number(query.get("bought")))
            refused = query.get("refused", "")
            return page(
                segment,
                "ingame.Item",
                visit=found,
                shelf=shelf,
                ware=ware,
                wearer=_wearer(game, ware, language_of(game, segment)),
                chosen=_chosen(ware, _number(query.get("pick"))),
                bought=bought,
                tag=bought.offer.product.ownership.tag(ware.item.id) if bought else "",
                equipping=query.get("equip") == "1",
                refused=refused if refused in _REFUSALS else "",
            )

        return await guarded(segment, answer())

    @router.post(STORE + "/{category}/{item}")
    async def buy(
        request: Request, segment: str, category: str, item: str, session_id: SessionId = None
    ) -> Response:
        async def answer() -> Response:
            found = await visit(request, segment, session_id)
            shelf, ware = _ware(found.window, category, item)
            if shelf is None or ware is None:
                return RedirectResponse(found.link(), 303)
            form = await request.form()
            point = ware.point(_number(str(form.get("product", ""))))
            price = _number(str(form.get("price", "")))
            sent = str(form.get("csrf", ""))
            if not hmac.compare_digest(found.token.encode(), sent.encode()):
                return RedirectResponse(found.link(category, item, refused=EXPIRED), 303)
            if point is None or point.offer.product.price != price:
                return RedirectResponse(found.link(category, item, refused=CHANGED), 303)
            if point.after is None:
                return RedirectResponse(found.link(category, item, refused=OWNED), 303)
            number = point.offer.product.id
            try:
                await purchases.purchase(game, found.persona, point.offer.product_id)
            except PurchaseRefused as refused:
                if refused.cause == INSUFFICIENT_FUNDS:
                    return RedirectResponse(found.link(category, item, "need", product=number), 303)
                cause = OWNED if refused.cause == ALREADY_OWNED else CHANGED
                return RedirectResponse(found.link(category, item, refused=cause), 303)
            # The game's count of a consumable's uses in the round grows by the uses bought.
            more = point.offer.product.ownership.uses or 0
            equipped = [
                f"{entry['item']}_{int(entry['uses']) + more}"
                if entry["uses"] and int(entry["item"]) == ware.item.id
                else entry[0]
                for entry in _entries(found.equipped)
            ]
            equip = shelf.category.equips and form.get("equip") == "1"
            if equip and not ware.equipped:
                # The game equips the item once told of it, so the next pages show it equipped.
                equipped.append(str(ware.item.id))
            found = replace(found, equipped=" ".join(equipped))
            values = {"bought": number, "equip": int(equip)}
            return RedirectResponse(found.link(category, item, **values), 303)

        return await guarded(segment, answer())

    @router.get(STORE + "/{category}/{item}/need")
    async def need(
        request: Request, segment: str, category: str, item: str, session_id: SessionId = None
    ) -> Response:
        async def answer() -> Response:
            found = await visit(request, segment, session_id)
            shelf, ware = _ware(found.window, category, item)
            if shelf is None or ware is None:
                return RedirectResponse(found.link(), 303)
            point = ware.point(_number(request.query_params.get("product")))
            if point is None or point.after is None or not point.short:
                return RedirectResponse(found.link(category, item), 303)
            alternative = next((other for other in ware.buyable if not other.short), None)
            rules = found.window.rules
            return page(
                segment,
                "ingame.Need",
                visit=found,
                shelf=shelf,
                ware=ware,
                point=point,
                alternative=alternative,
                help=rules.help.get(point.offer.product.currency, ""),
            )

        return await guarded(segment, answer())

    return router


def _entries(equipped: str) -> list[re.Match[str]]:
    entries = (_EQUIPPED.fullmatch(entry) for entry in re.split(r"[ +]", equipped) if entry)
    return [entry for entry in entries if entry][:_MOST_EQUIPPED]


def _number(text: str | None) -> int:
    return int(text) if text and text.isascii() and text.isdecimal() and len(text) <= 18 else 0


def _ware(window: StoreWindow, category: str, item: str) -> tuple[Shelf | None, Ware | None]:
    shelf = window.shelf(category)
    return shelf, shelf.ware(_number(item)) if shelf else None


def _chosen(ware: Ware, product: int) -> PricePoint | None:
    # The price point asked for, else the first one the account can pay, else the first.
    buyable = ware.buyable
    asked = next((point for point in buyable if point.offer.product.id == product), None)
    return (
        asked
        or next((point for point in buyable if not point.short), None)
        or (buyable[0] if buyable else None)
    )


def _wearer(game: Game, ware: Ware, language: str) -> str:
    # Who may use an item: any hero, those of a faction or of some classes, or both.
    assert game.heroes is not None
    item, factions, classes = ware.item, game.heroes.factions, game.heroes.classes
    names = [
        translate(f"games.{game.id}.class.{name}", language)
        for name in classes
        if name in item.classes
    ]
    any_class = item.classes >= classes.keys()
    if item.factions >= factions.keys():
        if any_class:
            return translate("web.ingame.for.anyone", language)
        return translate("web.ingame.for.classes", language) % {"classes": listed(names, language)}
    faction = next(name for name in factions if name in item.factions)
    if any_class:
        name = translate(f"games.{game.id}.faction.{faction}", language)
        return translate("web.ingame.for.faction", language) % {"faction": name}
    short = translate(f"games.{game.id}.faction.{faction}.short", language)
    return translate("web.ingame.for.both", language) % {
        "faction": short,
        "classes": listed(names, language),
    }


def _kit(game: Game) -> Kit:
    assert game.store is not None and game.store.in_game is not None and game.heroes is not None
    wallets = list(game.site.wallets) if game.site else []
    rules = [
        f'[data-coin="{wallet.currency}"]{{background-color:{wallet.fill}}}' for wallet in wallets
    ]
    rules += [
        f'[data-faction="{name}"]{{background-color:{faction.colour}}}'
        for name, faction in game.heroes.factions.items()
    ]
    css = "".join(inline(name) for name in _STYLES) + "\n".join(rules) + "\n"
    script = inline("store.js")
    policy = (
        f"default-src 'none'; style-src {digest(css)}; script-src {digest(script)}; "
        "img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return Kit(
        game=game,
        styles=Markup(f"<style>{css}</style>"),
        script=Markup(f"<script>{script}</script>"),
        wallets=wallets,
        coins={wallet.currency: wallet for wallet in wallets},
        headers={**HEADERS, "Content-Security-Policy": policy},
    )
