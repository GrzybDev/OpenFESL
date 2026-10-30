from dataclasses import dataclass, field
from functools import partial

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.game_pages import SiteWallet
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Heroes, Roster
from openfesl.core.rewards import Reward, Rewards, RewardState
from openfesl.core.wallets import Wallets
from openfesl.web.pages import Player

# The check of a hero name as it is typed, which a script asks for and which shows no page.
NAME_CHECK = "/heroes/name"
# The state of a start link, which the Play Now page asks for while it waits; it shows no page.
LINK_STATUS = "/play-now/status"
# The game's model of a hero with a look, which the creator's stage asks for; it shows no page.
FIGURE = "/heroes/figure"


@dataclass(frozen=True, slots=True)
class SiteBar:
    """What the bar of a game's own site shows the signed-in account.

    Attributes:
        wallets: The balances the account has to spend, each with how the bar draws it.
        roster: The account's heroes, in a game whose heroes are made on its site.
        funds: What the account has to pay the game's prices with, in such a game.
        rewards: Its game's rewards as the account sees them now, which its pages read.
    """

    wallets: list[tuple[SiteWallet, int]]
    roster: Roster | None
    funds: int
    rewards: list[Reward] = field(default_factory=list)

    @property
    def ready(self) -> int:
        """How many of its game's rewards the account may claim now."""
        return sum(reward.state is RewardState.CLAIMABLE for reward in self.rewards)


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

    def __init__(
        self, app: ASGIApp, games: Games, wallets: Wallets, heroes: Heroes, rewards: Rewards
    ) -> None:
        """Read the bars of the games that have a site of their own.

        Args:
            app: The application inside the player's session.
            games: The games the server serves.
            wallets: The players' wallets.
            heroes: The heroes of the games whose heroes are made on their site.
            rewards: The rewards of the games whose sites give them, which the menus count.
        """
        self._app = app
        self._games = {game.id: game for game in games if game.site}
        self._wallets = wallets
        self._heroes = heroes
        self._rewards = rewards

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
                checked = scope["path"] in (
                    f"/{game.id}{path}" for path in (NAME_CHECK, LINK_STATUS, FIGURE)
                )
                if scope["method"] == "GET" and not checked:
                    state["site_bar"] = await self._bar(game, player)
        await self._app(scope, receive, send)

    async def _bar(self, game: Game, player: Player) -> SiteBar:
        assert game.site is not None
        shown = game.site.wallets
        balances = await self._wallets.of_account(
            game, player.account_id, [wallet.currency for wallet in shown]
        )
        if not game.heroes:
            return SiteBar(list(zip(shown, balances, strict=True)), None, 0)
        roster = await self._heroes.roster(game, player.account_id)
        [funds] = await self._wallets.of_account(game, player.account_id, [game.heroes.currency])
        rewards = await self._rewards.available(game, player.account_id)
        return SiteBar(list(zip(shown, balances, strict=True)), roster, funds, rewards)
