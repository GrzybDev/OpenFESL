from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.catalogues import Catalogue, Catalogues, game_text
from openfesl.core.deal_offers import STATES, OfferStore, offer_state
from openfesl.core.games import Game
from openfesl.core.store import Bundle, ListedBundle
from openfesl.core.store_prices import PriceStore
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.deals import DealAdminViews
from openfesl.web.admin.offer_editor import CLOCK, OfferEditor, editor_of, game_named
from openfesl.web.admin.store import SECTION, store_url
from openfesl.web.pages import whole_number
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

# How long the panel lists an ended offer.
KEPT = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class OfferRow:
    """An admin offer as the panel's list shows it.

    Attributes:
        listed: Its bundle.
        name: Its name in the page's language.
        state: scheduled, live, sold for sold out, ended, or off while its section is switched
            off.
        share: How much its price takes off its items' list prices in percent; 0 for none.
        imported: Whether it takes the texts of the game's bundle with its items.
    """

    listed: ListedBundle
    name: str
    state: str
    share: int
    imported: bool


class DealOfferViews:
    """Puts together the admin's own offers among the hot deals and their editor."""

    def __init__(
        self,
        deals: DealAdminViews,
        offers: OfferStore,
        prices: PriceStore,
        catalogues: Catalogues,
    ) -> None:
        """Read and change the admin's offers, which sell products of the stores.

        Args:
            deals: The games' hot-deal rotations, whose sections list the offers.
            offers: Where the admin's offers are kept.
            prices: Hold every product of the stores, which the offers sell.
            catalogues: The games' imported catalogues, which name the items.
        """
        self.deals = deals
        self.offers = offers
        self._prices = prices
        self._catalogues = catalogues

    async def rows(self, game: Game, language: str) -> list[OfferRow]:
        """Return a game's admin offers, live first, with those ended in the last 90 days.

        Args:
            game: The game, which has hot deals.
            language: The language of the offers' names.
        """
        now = datetime.now(UTC)
        rules = await self.deals.rules(game)
        catalogue = await self._catalogues.of(game, language)
        rows = []
        for listed in await self.offers.offers(game.id, now - KEPT):
            bundle = listed.bundle
            worth = sum(member.price for member in listed.members)
            off = 100 * (worth - bundle.price) // worth if worth > bundle.price else 0
            rows.append(
                OfferRow(
                    listed,
                    offer_name(game, catalogue, bundle, language),
                    offer_state(bundle, now, rules),
                    bundle.discount or off,
                    game_named(rules, bundle),
                )
            )
        return sorted(rows, key=lambda row: STATES.index(row.state))

    async def editor(
        self,
        game: Game,
        number: int | None,
        language: str,
        fields: ImmutableMultiDict[str, Any] | None,
    ) -> OfferEditor:
        """Return an admin offer's editor, with its fields as written where a form sent them.

        Args:
            game: The game, which has hot deals.
            number: The offer's bundle number; none for a new offer.
            language: The page's language.
            fields: The form's fields; none for the offer as it is saved.

        Raises:
            HTTPException: 404 for a number that is no admin offer of the game, 400 for fields
                that no form of the editor sends.
        """
        rules = await self.deals.rules(game)
        listed = await self.offers.offer(game.id, number) if number is not None else None
        if number is not None and listed is None:
            raise HTTPException(404)
        texts = StoreTexts(game, language, datetime.now(UTC))
        catalogue = await self._catalogues.of(game, language)
        every = {product.id: product for product in await self._prices.every(game.id)}
        return editor_of(texts, rules, catalogue, every, listed, fields)


def offer_name(game: Game, catalogue: Catalogue, bundle: Bundle, language: str) -> str:
    """Return an admin offer's name in a language, its own or the game's.

    Args:
        game: The game.
        catalogue: The game's imported catalogue, in the language.
        bundle: The offer's bundle.
        language: The language code.
    """
    own, _ = bundle.texts.get(language, (None, None))
    return own or game_text(game, catalogue, bundle.name, language, bundle.faction)


def offer_routes(sessions: AdminSessions, views: DealOfferViews) -> APIRouter:
    """Return the admin's own offers among each game's hot deals, with their editor.

    Every role reads them; adding, changing, restocking and ending them needs the admin role.
    An offer is sold from its start to its end beside the rotation's deals while its section is
    on, also while the rotation is off, and while its stock lasts.
    The add panel and the dropping of items answer the form without saving it.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and keep the offers.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/deals/offers"

    @router.get(path, response_class=HTMLResponse)
    async def offers(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.deals.stores.game(game_id)
        rows = await views.rows(game, request.state.language)
        return await sessions.render(
            request,
            "admin.DealOffers",
            admin=admin,
            of=game,
            rows=rows,
            manages=admin.role is Role.ADMIN,
        )

    async def page(
        request: Request, admin: Admin, game: Game, number: int | None, status: int = 200
    ) -> HTMLResponse:
        # Live answers to the fields as they are typed leave out the buttons that change items.
        query = [(name, value) for name, value in request.query_params.multi_items()]
        typed = [(name, value) for name, value in query if name not in ("add", "drop")]
        fields: ImmutableMultiDict[str, Any] | None = (
            await request.form() if request.method == "POST" else ImmutableMultiDict(typed)
        )
        if not query and request.method != "POST":
            fields = None
        found = await views.editor(game, number, request.state.language, fields)
        return await sessions.render(
            request,
            "admin.DealOffer",
            status,
            admin=admin,
            found=found,
            refusing=status == 400,
            manages=admin.role is Role.ADMIN,
        )

    @router.get(path + "/new", response_class=HTMLResponse)
    async def new(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        return await page(request, admin, views.deals.stores.game(game_id), None)

    @router.get(path + "/{number}", response_class=HTMLResponse)
    async def offer(request: Request, game_id: str, number: int) -> HTMLResponse:
        admin = sessions.admin(request)
        return await page(request, admin, views.deals.stores.game(game_id), number)

    async def save(request: Request, game_id: str, number: int | None) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.deals.stores.game(game_id)
        if any(name in form for name in ("find", "add", "drop")):
            return await page(request, admin, game, number)
        found = await views.editor(game, number, request.state.language, form)
        if found.bundle is None:
            return await page(request, admin, game, number, 400)
        bundle = found.bundle
        details = {
            "game": game.id,
            "name": bundle.name,
            "faction": bundle.faction,
            "section": bundle.section,
            "price": bundle.price,
            "currency": bundle.currency,
            "starts": f"{bundle.starts:{CLOCK}}",
            "ends": f"{bundle.ends:{CLOCK}}",
            "items": len(bundle.members),
            "new": number is None,
        }
        saved = await views.offers.save(
            game.id, number, bundle, AdminAction.DEAL_OFFER, details, admin
        )
        if saved is None:
            raise HTTPException(404)
        if found.rules.sections[found.draft.section].enabled:
            texts = StoreTexts(game, request.state.language, datetime.now(UTC))
            assert bundle.ends is not None
            ends = texts.moment(bundle.ends)
            toast(request, "web.admin.toast.deals.offer", offer=found.name, ends=ends)
        else:
            toast(request, "web.admin.toast.deals.offer.off", offer=found.name)
        return RedirectResponse(store_url(request, game, "/deals/offers"), 303)

    @router.post(path + "/new")
    async def add(request: Request, game_id: str) -> Response:
        return await save(request, game_id, None)

    @router.post(path + "/{number}")
    async def change(request: Request, game_id: str, number: int) -> Response:
        return await save(request, game_id, number)

    async def stocked(request: Request, game: Game, number: int) -> OfferRow:
        row = await _row(views, game, number, request.state.language)
        if row.listed.bundle.stock is None or row.state == "ended":
            raise HTTPException(404)
        return row

    @router.get(path + "/{number}/restock", response_class=HTMLResponse)
    async def restocking(request: Request, game_id: str, number: int) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.deals.stores.game(game_id)
        row = await stocked(request, game, number)
        return await sessions.render(
            request, "admin.DealOfferRestock", admin=admin, of=game, row=row, count="", why=""
        )

    @router.post(path + "/{number}/restock")
    async def restock(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.deals.stores.game(game_id)
        row = await stocked(request, game, number)
        bundle = row.listed.bundle
        assert bundle.stock is not None
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        written = str(form.get("count", "")).strip()
        most = MOST_BALANCE - bundle.stock
        count = whole_number(written, most)
        if count is None:
            why = texts.text("web.admin.deals.offer.refused.restock", most=texts.amount(most))
            return await sessions.render(
                request,
                "admin.DealOfferRestock",
                400,
                admin=admin,
                of=game,
                row=row,
                count=written,
                why=why,
            )
        details = {"game": game.id, "name": bundle.name, "faction": bundle.faction, "count": count}
        await views.offers.restock(game.id, number, count, details, admin)
        toast(request, "web.admin.toast.deals.restocked", offer=row.name, count=texts.amount(count))
        return RedirectResponse(store_url(request, game, "/deals/offers"), 303)

    @router.get(path + "/{number}/end", response_class=HTMLResponse)
    async def ending(request: Request, game_id: str, number: int) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.deals.stores.game(game_id)
        row = await _row(views, game, number, request.state.language)
        return await sessions.render(request, "admin.DealOfferEnd", admin=admin, of=game, row=row)

    @router.post(path + "/{number}/end")
    async def end(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        check_form(request, await request.form())
        game = views.deals.stores.game(game_id)
        row = await _row(views, game, number, request.state.language)
        bundle = row.listed.bundle
        if row.state != "ended":
            now = datetime.now(UTC).replace(microsecond=0)
            details = {"game": game.id, "name": bundle.name, "faction": bundle.faction}
            details["ends"] = f"{bundle.ends:{CLOCK}}" if bundle.ends else None
            ended = replace(bundle, ends=now)
            await views.offers.save(game.id, number, ended, AdminAction.DEAL_ENDED, details, admin)
            toast(request, "web.admin.toast.deals.ended", offer=row.name)
        return RedirectResponse(store_url(request, game, "/deals/offers"), 303)

    return router


async def _row(views: DealOfferViews, game: Game, number: int, language: str) -> OfferRow:
    row = next((row for row in await views.rows(game, language) if row.listed.id == number), None)
    if row is None:
        raise HTTPException(404)
    return row
