from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.chests import Chests
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.purchases import INSUFFICIENT_FUNDS, PurchaseRefused, Purchases
from openfesl.core.texts import localize
from openfesl.core.wallets import Wallets
from openfesl.web.account.session import open_page, signed_in
from openfesl.web.chests import CHESTS, held_keys
from openfesl.web.pages import Pages
from openfesl.web.sessions import SentForms, form_token, notify, purchase_matches, toast
from openfesl.web.store_texts import StoreTexts

KEYS = CHESTS + "/keys"


class KeyPages:
    """The purchase of treasure chest keys for an account, a few at a time, with its Funds."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        chests: Chests,
        sent: SentForms,
        purchases: Purchases,
        entitlements: EntitlementStore,
        wallets: Wallets,
    ) -> None:
        """Prepare the key purchase of each game that lists the treasure chests page.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the treasure chests page sell keys.
            chests: Tells which product sells the keys.
            sent: Lets each form that buys keys buy once.
            purchases: Sells them to the account.
            entitlements: Hold the account's keys.
            wallets: Tell what the account has to pay with, opening nothing.
        """
        self._pages = pages
        self._games = games
        self._chests = chests
        self._sent = sent
        self._purchases = purchases
        self._entitlements = entitlements
        self._wallets = wallets

    def routes(self) -> APIRouter:
        """Return each game's page and form that buy keys, which the chests page's dialog holds."""
        router = APIRouter()
        for game in self._games.with_page("treasure-chests"):
            path = f"/{game.id}{KEYS}"
            router.add_api_route(path, self._page(game), response_class=HTMLResponse)
            router.add_api_route(path, self._buy(game), methods=["POST"])
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_keys(request: Request) -> Response:
            player = signed_in(request)
            if player is None:
                notify(request, "needed", page="keys")
                return open_page(
                    request, "/sign-in", localize(f"/{game.id}{KEYS}", request.state.language)
                )
            assert game.chests is not None
            key = await self._chests.key(game)
            funds = 0
            if key:
                [funds] = await self._wallets.of_account(game, player.account_id, [key.currency])
            return self._pages.render(
                request,
                "chests.Keys",
                game=game,
                key=key,
                keys=await held_keys(self._entitlements, game.chests, player.account_id),
                funds=funds,
                csrf=form_token(request),
            )

        return show_keys

    def _buy(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def buy_keys(request: Request) -> Response:
            form = await request.form()
            language = request.state.language
            page = localize(f"/{game.id}{CHESTS}", language)
            player = signed_in(request)
            if player is None or not purchase_matches(request, form):
                toast(request, "web.chests.keys.refused.form", error=True)
                return RedirectResponse(page, 303)
            assert game.chests is not None and game.store is not None
            chosen = str(form.get("keys", ""))
            count = int(chosen) if chosen.isascii() and chosen.isdecimal() else 0
            key = await self._chests.key(game)
            if key is None or count not in game.chests.packs:
                toast(request, "web.chests.keys.refused.unsold", error=True)
                return RedirectResponse(page, 303)
            texts = StoreTexts(game, language, datetime.now(UTC))
            if str(form.get("price", "")) != str(key.price):
                each = texts.priced(key.price, key.currency)
                toast(request, "web.chests.keys.refused.price", error=True, price=each)
                return RedirectResponse(page, 303)
            price = texts.priced(key.price * count, key.currency)
            try:
                async with self._sent.once(form) as bought:
                    if bought:
                        toast(request, "web.chests.keys.done.again", error=False)
                        return RedirectResponse(page, 303)
                    await self._purchases.purchase_for_account(
                        game, player.account_id, game.store.product_id(key.id), count
                    )
            except PurchaseRefused as refused:
                if refused.cause != INSUFFICIENT_FUNDS:
                    toast(request, "web.chests.keys.refused.unsold", error=True)
                    return RedirectResponse(page, 303)
                [funds] = await self._wallets.of_account(game, player.account_id, [key.currency])
                cost = texts.count("web.chests.keys.cost", count, price=price)
                values = {"funds": texts.priced(funds, key.currency), "cost": cost}
                toast(request, "web.chests.keys.refused.funds", error=True, **values)
                return RedirectResponse(page, 303)
            keys = texts.count("web.chests.keys.bought", count)
            toast(
                request,
                "web.chests.keys.done",
                error=False,
                keys=keys,
                price=price,
                bought=chosen,
            )
            return RedirectResponse(page, 303)

        return buy_keys
