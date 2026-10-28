from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.game_pages import SiteWallet
from openfesl.core.games import Game, Games
from openfesl.core.wallets import Wallets
from openfesl.web.pages import Player


@dataclass(frozen=True, slots=True)
class SiteBar:
    """What the bar of a game's own site shows the signed-in account.

    Attributes:
        wallets: The balances the account has to spend, each with how the bar draws it.
    """

    wallets: list[tuple[SiteWallet, int]]


class SiteBarMiddleware:
    """Reads what the bar of a game's own site shows, for the pages of a signed-in account.

    The pages of a game with a site of its own, whatever router serves them, find it in the
    request's state as site_bar.
    """

    def __init__(self, app: ASGIApp, games: Games, wallets: Wallets) -> None:
        """Read the bars of the games that have a site of their own.

        Args:
            app: The application inside the player's session.
            games: The games the server serves.
            wallets: The players' wallets.
        """
        self._app = app
        self._games = {game.id: game for game in games if game.site}
        self._wallets = wallets

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer a request with the bar of its game's site in its state, when it has one.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        # A form's answer only leads to a page, which reads the bar when it is opened.
        if scope["type"] == "http" and scope["method"] == "GET":
            state = scope.setdefault("state", {})
            player: Player | None = state.get("player")
            game = self._games.get(scope["path"].split("/")[1])
            if player and game:
                state["site_bar"] = await self._bar(game, player)
        await self._app(scope, receive, send)

    async def _bar(self, game: Game, player: Player) -> SiteBar:
        assert game.site is not None
        shown = game.site.wallets
        balances = await self._wallets.of_account(
            game, player.account_id, [wallet.currency for wallet in shown]
        )
        return SiteBar(list(zip(shown, balances, strict=True)))
