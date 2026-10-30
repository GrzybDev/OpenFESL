from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.catalogues import Catalogues
from openfesl.core.census import Census
from openfesl.core.deals import Deal, HotDeals
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.hero_boards import HeroBoards
from openfesl.core.reward_runs import Run
from openfesl.core.rewards import Reward, Rewards, RewardState
from openfesl.core.store import ProductStore
from openfesl.web.account.session import signed_in
from openfesl.web.boards import Board, lines, shares, standings
from openfesl.web.hot_deals import deal_card
from openfesl.web.pages import Pages
from openfesl.web.reward_texts import RewardTexts, reward_texts
from openfesl.web.site import site_bar
from openfesl.web.store_texts import StoreTexts

# How many heroes the front page names, best first.
TOP = 5


@dataclass(frozen=True, slots=True)
class Gifts:
    """A faction's part of the event that runs, as the front page says where it stands.

    Attributes:
        faction: The faction; none for the account's own part.
        state: What it is to the account: waiting to be claimed, claimed, or out of reach.
        text: Whom it waits for, whom it went to, or what it needs.
    """

    faction: str | None
    state: str
    text: str


class HeroesFrontPages:
    """The front page of a game whose heroes are made on its site: what happens there this week."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        census: Census,
        rewards: Rewards,
        deals: HotDeals,
        boards: HeroBoards,
        stores: tuple[Catalogues, EntitlementStore, ProductStore],
    ) -> None:
        """Prepare the front page of each game with heroes and a site of its own.

        Args:
            pages: Renders the pages.
            games: The games, of which those with heroes and a site get this front page.
            census: Counts the players online and in matches.
            rewards: Tell the event that runs and where its gifts stand for the account.
            deals: List the deals of the day.
            boards: Rank the heroes by this week's score, as the leaderboards do.
            stores: The imported catalogues, the heroes' items and the store's boxes, which
                name the event's gifts and the deals' items.
        """
        self._pages = pages
        self._games = games
        self._census = census
        self._rewards = rewards
        self._deals = deals
        self._boards = boards
        self._stores = stores

    def routes(self) -> APIRouter:
        """Return the front page of each game with heroes and a site of its own."""
        router = APIRouter()
        for game in self._games:
            if game.heroes and game.site:
                router.add_api_route(f"/{game.id}", self._page(game), response_class=HTMLResponse)
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_front(request: Request) -> Response:
            language = request.state.language
            player = signed_in(request)
            bar = await site_bar(request)
            roster = bar.roster if bar else None
            rules = await self._rewards.rules(game)
            catalogues, _, products = self._stores
            art = self._pages.shows_art(request, game)
            event, next_event = None, None
            if rules:
                words = await reward_texts(request, game, rules, catalogues, art)
                running = await self._rewards.running(game)
                # The event of daily gifts first, as its gifts change each day.
                run = min(running, key=lambda run: not run.event.daily, default=None)
                if run:
                    gifts = [Gifts(part.faction, "", "") for part in run.event.parts.values()]
                    if bar and player and roster and roster.heroes:
                        gifts = _gifts(words, run, bar.rewards)
                    event = (words.event(run), run, gifts, words.daily(running))
                else:
                    upcoming = await self._rewards.upcoming(game)
                    next_event = words.event(upcoming[0]) if upcoming else None
            texts = StoreTexts(game, language, datetime.now(UTC))
            catalogue = await catalogues.of(game, language)
            deals = await self._daily_deals(game, language)
            deal_rules = await self._deals.rules(game) if deals else None
            heroes = roster.heroes if roster else None
            cards = [
                await deal_card(texts, catalogue, products, deal, heroes, art, deal_rules, set())
                for deal in deals
            ]
            ends = deals[0].listed.bundle.ends if deals else None
            return self._pages.render(
                request,
                "heroes.Front",
                game=game,
                event=event,
                next_event=next_event,
                **await self._top(game, language),
                deals=cards,
                ends=ends,
                left=texts.countdown(ends) if ends else "",
                activity=self._census.activity(game),
                counted=datetime.now(UTC),
                roster=roster,
            )

        return show_front

    async def _top(self, game: Game, language: str) -> dict[str, Any]:
        # The first heroes of the week's score board, with the factions' shares of the board and
        # when it was ranked.
        if "leaderboards" not in game.pages:
            return {"top": [], "shares": [], "ranked": None}
        board = Board()
        found = await standings(self._boards, game, board)
        return {
            "top": lines(game, board, found.lines[:TOP], language),
            "shares": shares(game, found.lines),
            "ranked": found.ranked,
        }

    async def _daily_deals(self, game: Game, language: str) -> list[Deal]:
        section = game.site.front_deals if game.site else None
        if section is None:
            return []
        deals = await self._deals.hot_deals(game, language)
        return [deal for deal in deals if deal.listed.bundle.section == section]


def _gifts(words: RewardTexts, run: Run, rewards: list[Reward]) -> list[Gifts]:
    # Where each part of the event's gifts stands for the account, today's for a daily event:
    # waiting for whom, claimed for whom, counting towards its goal, or out of reach for want of
    # a hero of its faction.
    texts, daily = words.texts, run.event.daily
    found = []
    for reward in rewards:
        if reward.event != run.key or reward.kept or (daily and reward.day != run.day):
            continue
        if reward.claim:
            hero = words.hero(reward.claim.hero)
            key = "web.front.event.claimed" if daily else "web.front.event.claimed.once"
            text = texts.text(key, hero=hero.persona.name if hero else "")
        elif reward.state is RewardState.CLAIMABLE:
            names = [h.persona.name for p in reward.heroes if (h := words.hero(p))]
            key = "web.front.event.waits" if daily else "web.front.event.waits.once"
            text = texts.text(key, heroes=texts.alternatives(names))
        elif reward.goal is not None and reward.heroes and reward.ends:
            when = f"{words.day(reward.ends)}, {words.clock(reward.ends)}"
            text = texts.text("web.rewards.state.progress", when=when)
        else:
            faction = texts.factions(frozenset({reward.faction})) if reward.faction else ""
            text = texts.text("web.rewards.state.needs", faction=faction)
        found.append(Gifts(reward.faction, reward.state.value, text))
    return found
