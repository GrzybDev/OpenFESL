from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.calendar import today
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.reward_claims import Claimed
from openfesl.core.rewards import Reward, Rewards, RewardState
from openfesl.core.store import ProductStore
from openfesl.core.texts import localize
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages, listed
from openfesl.web.reward_cards import RewardCards, reward_cards
from openfesl.web.sessions import form_token

REWARDS = "/rewards"
# How many of the next events the page names.
NEXT_EVENTS = 2


class RewardsPages:
    """The rewards page of a game's site: what the account may claim, and what it claimed."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        rewards: Rewards,
        catalogues: Catalogues,
        products: ProductStore,
    ) -> None:
        """Prepare the rewards page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            rewards: Lists and gives the rewards.
            catalogues: Name and draw the items the rewards give, once imported.
            products: Hold the boxes that daily gifts give from.
        """
        self._pages = pages
        self._games = games
        self._rewards = rewards
        self._catalogues = catalogues
        self._products = products

    def routes(self) -> APIRouter:
        """Return the rewards page of each game that lists it."""
        router = APIRouter()
        for game in self._games.with_page("rewards"):
            path = f"/{game.id}"
            router.add_api_route(path + REWARDS, self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_rewards(request: Request) -> Response:
            cards = await self._cards(game, request)
            words = cards.words
            texts, rules = words.texts, words.rules
            running = await self._rewards.running(game)
            upcoming = [words.event(run) for run in await self._rewards.upcoming(game)]
            player = signed_in(request)
            # The milestone that counts the account's heroes up to its first one.
            first_hero = next(
                (m for m in rules.milestones.values() if m.enabled and not m.stats and m.goal == 1),
                None,
            )
            coming = [
                texts.text("web.rewards.coming_up.event", event=event.name, date=event.start)
                for event in upcoming[:NEXT_EVENTS]
            ]
            available: list[Reward] = []
            history: list[Claimed] = []
            if words.roster and player:
                available = await self._rewards.available(game, player.account_id)
                history = await self._rewards.history(game, player.account_id)
            return self._pages.render(
                request,
                "rewards.Rewards",
                game=game,
                events=[words.event(run) for run in running],
                upcoming=upcoming[:1],
                coming=listed(coming, texts.language) if coming else "",
                first_hero=words.funds(first_hero.gift.funds) if first_hero else "",
                sections=await cards.sections(available, running),
                past=[words.past(claimed) for claimed in history] + words.lapsed(history),
                claimed=len(history),
                ready=sum(reward.state is RewardState.CLAIMABLE for reward in available),
                daily=words.daily(running),
                year=str(today(words.now, rules.zone).year),
                next=localize(f"/{game.id}{REWARDS}", texts.language),
                csrf=form_token(request) if player else "",
            )

        return show_rewards

    async def _cards(self, game: Game, request: Request) -> RewardCards:
        rules = await self._rewards.rules(game)
        assert rules is not None
        art = self._pages.shows_art(request, game)
        return await reward_cards(request, game, rules, self._catalogues, self._products, art)
