from dataclasses import dataclass
from functools import partial

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.game_pages import SiteWallet
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Heroes, Roster
from openfesl.core.wallets import Wallets
from openfesl.web.pages import Player

# The check of a hero name as it is typed, which a script asks for and which shows no page.
NAME_CHECK = "/heroes/name"


@dataclass(frozen=True, slots=True)
class SiteBar:
    """What the bar of a game's own site shows the signed-in account.

    Attributes:
        wallets: The balances the account has to spend, each with how the bar draws it.
        roster: The account's heroes, in a game whose heroes are made on its site.
    """

    wallets: list[tuple[SiteWallet, int]]
    roster: Roster | None


async def site_bar(request: Request) -> SiteBar | None:
    """Return the bar of a game's own site that a signed-in account's page shows, read once.

    Args:
        request: The request the page answers, such as a form's that shows its page again.
    """
    state = request.scope.setdefault("state", {})
    if "site_bar" not in state and "read_site_bar" in state:
        state["site_bar"] = await state["read_site_bar"]()
    bar: SiteBar | None = state.get("site_bar")
    return bar


class SiteBarMiddleware:
    """Reads what the bar of a game's own site shows, for the pages of a signed-in account.

    The pages of a game with a site of its own, whatever router serves them, find it in the
    request's state as site_bar; a form's answer reads it through site_bar only when it shows a
    page.
    """

    def __init__(self, app: ASGIApp, games: Games, wallets: Wallets, heroes: Heroes) -> None:
        """Read the bars of the games that have a site of their own.

        Args:
            app: The application inside the player's session.
            games: The games the server serves.
            wallets: The players' wallets.
            heroes: The heroes of the games whose heroes are made on their site.
        """
        self._app = app
        self._games = {game.id: game for game in games if game.site}
        self._wallets = wallets
        self._heroes = heroes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer a request with the bar of its game's site in its state, when it has one.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            player: Player | None = state.get("player")
            game = self._games.get(scope["path"].split("/")[1])
            if player and game:
                state["read_site_bar"] = partial(self._bar, game, player)
                checked = scope["path"] == f"/{game.id}{NAME_CHECK}"
                if scope["method"] == "GET" and not checked:
                    state["site_bar"] = await self._bar(game, player)
        await self._app(scope, receive, send)

    async def _bar(self, game: Game, player: Player) -> SiteBar:
        assert game.site is not None
        shown = game.site.wallets
        balances = await self._wallets.of_account(
            game, player.account_id, [wallet.currency for wallet in shown]
        )
        roster = await self._heroes.roster(game, player.account_id) if game.heroes else None
        return SiteBar(list(zip(shown, balances, strict=True)), roster)
