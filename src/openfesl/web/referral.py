from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Heroes
from openfesl.core.rewards import Rewards
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages

REF = "/ref"
# The session's key of the personal link a visitor arrived through, which registration records.
REFERRAL = "referral"


@dataclass(frozen=True, slots=True)
class Referrer:
    """The account whose personal link a visitor arrived through.

    Attributes:
        game: The game whose Recruit a Friend the link belongs to.
        account: The account.
    """

    game: Game
    account: Account


class ReferralLinks:
    """The personal links of Recruit a Friend: the landing they open and who they name.

    A link names an account by its number, as its player profile does; a visitor signed out who
    arrives through a link of a public account while the programme runs carries the link in the
    session to registration, which records the new account as that account's recruit.
    """

    def __init__(
        self,
        pages: Pages,
        games: Games,
        rewards: Rewards,
        heroes: Heroes,
        accounts: Callable[[int], Awaitable[Account | None]],
    ) -> None:
        """Prepare the links of each game that lists Recruit a Friend.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing Recruit a Friend get links.
            rewards: Tell whether the programme runs, and its steps.
            heroes: Tell the default hero of the account a link names.
            accounts: Look up an account by its number.
        """
        self._pages = pages
        self._games = games.with_page("recruit-a-friend")
        self._rewards = rewards
        self._heroes = heroes
        self._accounts = accounts

    def routes(self) -> APIRouter:
        """Return the landing of each game's personal links."""
        router = APIRouter()
        for game in self._games:
            router.add_api_route(
                f"/{game.id}{REF}/{{account_id:int}}",
                self._landing(game),
                response_class=HTMLResponse,
            )
        return router

    async def referrer(self, request: Request) -> Referrer | None:
        """Return whose personal link a visitor arrived through, while it may still recruit.

        Args:
            request: A request of the visitor's session.
        """
        found = request.session.get(REFERRAL)
        game = next((g for g in self._games if found and g.id == found["game"]), None)
        if found is None or game is None:
            return None
        account = await self._recruiter(game, found["account"])
        return Referrer(game, account) if account else None

    async def _recruiter(self, game: Game, account_id: int) -> Account | None:
        # The account a link names, while the programme runs and others see the account.
        rules = await self._rewards.rules(game)
        if not (rules and rules.referral and rules.referral.enabled):
            return None
        account = await self._accounts(account_id)
        return account if account and account.public else None

    def _landing(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def show_landing(request: Request, account_id: int) -> Response:
            player = signed_in(request)
            account = await self._recruiter(game, account_id)
            hero = None
            if player:
                # Only accounts made through the link count, so a signed-in visit records nothing.
                kind = "own" if player.account_id == account_id else "signed_in"
            elif account:
                kind = "referrer"
                request.session[REFERRAL] = {"game": game.id, "account": account_id}
                hero = (await self._heroes.roster(game, account_id)).playing
            else:
                kind = "plain"
                request.session.pop(REFERRAL, None)
            return self._pages.render(
                request,
                "referral.Landing",
                game=game,
                kind=kind,
                referrer=account,
                hero=hero,
            )

        return show_landing
