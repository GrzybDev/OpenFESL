from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.catalogues import Catalogues
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.rewards import Rewards
from openfesl.core.store import ProductStore
from openfesl.core.wallets import Wallets
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages
from openfesl.web.reward_cards import reward_cards
from openfesl.web.site import site_bar


class FundsPages:
    """The Funds page of a game's site: the balances, and where Funds come from on this server."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        wallets: Wallets,
        rewards: Rewards,
        catalogues: Catalogues,
        entitlements: EntitlementStore,
        products: ProductStore,
    ) -> None:
        """Prepare the Funds page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those with the page, heroes and a site get it.
            wallets: Read the heroes' points without opening their wallets.
            rewards: Tell how the game gives rewards, the milestones that give Funds among them.
            catalogues: Name what rewards give, once imported.
            entitlements: Hold what the heroes own.
            products: Hold the boxes that rewards give from.
        """
        self._pages = pages
        self._games = games
        self._wallets = wallets
        self._rewards = rewards
        self._stores = catalogues, entitlements, products

    def routes(self) -> APIRouter:
        """Return each game's Funds page, at the address of the game's own website."""
        router = APIRouter()
        for game in self._games.with_page("funds"):
            if game.heroes and game.site:
                path = f"/{game.id}{game.site.path('funds')}"
                router.add_api_route(path, self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_funds(request: Request) -> Response:
            rules = game.heroes
            assert rules is not None
            bar = await site_bar(request)
            player = signed_in(request)
            heroes = bar.roster.heroes if bar and bar.roster else []
            points = await self._wallets.of_personas(
                game, [hero.persona for hero in heroes], rules.points
            )
            milestones = []
            if rewards := await self._rewards.rules(game):
                art = self._pages.shows_art(request, game)
                cards = await reward_cards(request, game, rewards, *self._stores, art)
                milestones = await cards.milestones(bar.rewards if bar and player else [])
            return self._pages.render(
                request,
                "store.Funds",
                game=game,
                # The game's Get Funds buttons open the page, through its website's address.
                from_game=request.query_params.get("from") == "game",
                points=list(zip(heroes, points, strict=True)),
                allowance=game.currencies[rules.currency].allowance,
                given=await self._wallets.allowance_given(game, player.account_id, rules.currency)
                if bar and player
                else None,
                milestones=milestones,
            )

        return show_funds
