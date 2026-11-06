from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.catalogues import Catalogues
from openfesl.core.claw import Claw
from openfesl.core.game_pages import SiteWallet
from openfesl.core.games import Game, Games
from openfesl.core.hero_rules import HeroRules
from openfesl.core.heroes import Heroes, Roster
from openfesl.core.rewards import Reward, Rewards, RewardState
from openfesl.core.texts import translate
from openfesl.core.wallets import Wallets
from openfesl.web.game_icons import ICON_PATH
from openfesl.web.pages import Player

# The check of a hero name as it is typed, which a script asks for and which shows no page.
NAME_CHECK = "/heroes/name"
# The state of a start link, which the Play Now page asks for while it waits; it shows no page.
LINK_STATUS = "/play-now/status"
# The game's model of a hero with a look, which the creator's stage asks for; it shows no page.
FIGURE = "/heroes/figure"
# The page that hands the game over to the launcher, the hero creator and the Daily Claw.
PLAY_NOW = "/play-now"
CREATOR = "/user/createHero"
CLAW = "/claw"
# The session's record of the rewards' day on which the account closed the Claw.
CLAW_CLOSED = "claw_closed"
# The pages that the Claw never opens by itself over: the launcher's hand-off, the creator, which
# the Claw sends an account without heroes to, and the Claw's own.
QUIET = (PLAY_NOW, CREATOR, CLAW)


@dataclass(frozen=True, slots=True)
class ClawEntry:
    """The Daily Claw as the menus and the frame of a game's own site show it, while it runs.

    Attributes:
        name: Its name in the page's language: the game's own where the catalogue has it.
        waiting: How many of the day's free grabs the account has left; 0 for a visitor.
        opens: Whether it opens by itself over the page: a free grab waits, the account did not
            close it on this day, and the page is one it covers.
        day: The rewards' day, on which closing it keeps it closed.
        next_free: When the next day's free grabs come.
    """

    name: str
    waiting: int = 0
    opens: bool = False
    day: date | None = None
    next_free: datetime | None = None


@dataclass(frozen=True, slots=True)
class SiteBar:
    """What the bar of a game's own site shows the signed-in account.

    Attributes:
        wallets: The balances the account has to spend, each with how the bar draws it.
        roster: The account's heroes, in a game whose heroes are made on its site.
        funds: What the account has to pay the game's prices with, in such a game.
        rewards: Its game's rewards as the account sees them now, which its pages read.
        paused: Whether claims of its game's rewards are paused.
        heroes: How its game's heroes are made and kept, at the prices of this moment, in a
            game whose heroes are made on its site.
        claw: Its game's Daily Claw while it runs.
    """

    wallets: list[tuple[SiteWallet, int]]
    roster: Roster | None
    funds: int
    rewards: list[Reward] = field(default_factory=list)
    paused: bool = False
    heroes: HeroRules | None = None
    claw: ClawEntry | None = None

    @property
    def ready(self) -> int:
        """How many of its game's rewards the account may claim now; none while claims pause."""
        claimable = sum(reward.state is RewardState.CLAIMABLE for reward in self.rewards)
        return 0 if self.paused else claimable


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
    page. A visitor's pages find the Daily Claw that the menus list as claw.
    """

    def __init__(
        self,
        app: ASGIApp,
        games: Games,
        wallets: Wallets,
        heroes: Heroes,
        rewards: Rewards,
        claw: Claw,
        catalogues: Catalogues,
    ) -> None:
        """Read the bars of the games that have a site of their own.

        Args:
            app: The application inside the player's session.
            games: The games the server serves.
            wallets: The players' wallets.
            heroes: The heroes of the games whose heroes are made on their site.
            rewards: The rewards of the games whose sites give them, which the menus count.
            claw: The Daily Claw of the games that have one, which the menus list and count.
            catalogues: The games' imported catalogues, which name the Claw.
        """
        self._app = app
        self._games = {game.id: game for game in games if game.site}
        self._wallets = wallets
        self._heroes = heroes
        self._rewards = rewards
        self._claw = claw
        self._catalogues = catalogues

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
            # Requests that show no page, as the game's icon that Discord's image proxy fetches.
            checked = game and scope["path"] in (
                ICON_PATH.format(game=game.id),
                *(f"/{game.id}{path}" for path in (NAME_CHECK, LINK_STATUS, FIGURE)),
            )
            if player and game:
                state["read_site_bar"] = partial(self._bar, game, player, scope)
                if scope["method"] == "GET" and not checked:
                    state["site_bar"] = await self._bar(game, player, scope)
            elif game and scope["method"] == "GET" and not checked:
                state["claw"] = await self._entry(game, None, scope)
        await self._app(scope, receive, send)

    async def _entry(self, game: Game, player: Player | None, scope: Scope) -> ClawEntry | None:
        # The Claw while it runs, named in the page's language, with what waits for an account.
        rules = await self._claw.running(game)
        if rules is None:
            return None
        language = scope["state"]["language"]
        catalogue = await self._catalogues.of(game, language)
        name = catalogue.text(rules.name) or translate("web.claw.name", language)
        if player is None:
            return ClawEntry(name)
        day = await self._claw.day(game, rules, player.account_id)
        closed = scope.get("session", {}).get(CLAW_CLOSED) == day.day.isoformat()
        quiet = scope["path"].startswith(tuple(f"/{game.id}{path}" for path in QUIET))
        opens = bool(day.waiting) and not closed and not quiet
        return ClawEntry(name, day.waiting, opens, day.day, day.next_free)

    async def _bar(self, game: Game, player: Player, scope: Scope) -> SiteBar:
        assert game.site is not None
        shown = game.site.wallets
        balances = await self._wallets.of_account(
            game, player.account_id, [wallet.currency for wallet in shown]
        )
        wallets = list(zip(shown, balances, strict=True))
        if not game.heroes:
            return SiteBar(wallets, None, 0)
        roster = await self._heroes.roster(game, player.account_id)
        [funds] = await self._wallets.of_account(game, player.account_id, [game.heroes.currency])
        rewards = await self._rewards.available(game, player.account_id)
        rules = await self._rewards.rules(game)
        paused = rules is not None and rules.paused
        heroes = await self._heroes.rules(game)
        claw = await self._entry(game, player, scope)
        return SiteBar(wallets, roster, funds, rewards, paused, heroes, claw)
