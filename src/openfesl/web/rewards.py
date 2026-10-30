from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.calendar import today
from openfesl.core.catalogues import Catalogues
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game, Games
from openfesl.core.reward_claims import Claimed
from openfesl.core.rewards import Reward, RewardRefused, Rewards
from openfesl.core.store import ProductStore
from openfesl.core.texts import localize
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages, listed
from openfesl.web.reward_cards import RewardCards, reward_cards
from openfesl.web.sessions import form_matches, form_token, toast
from openfesl.web.site import site_bar

REWARDS = "/rewards"
CLAIM = "/rewards/claim"
# How many of the next events the page names.
NEXT_EVENTS = 2
# The session's key of what the box of a gift just claimed gave, which the next page reveals.
REVEAL = "reward_reveal"


class RewardsPages:
    """The rewards page of a game's site: what the account may claim, and its claims."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        rewards: Rewards,
        catalogues: Catalogues,
        entitlements: EntitlementStore,
        products: ProductStore,
    ) -> None:
        """Prepare the rewards page of each game that lists it.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing the page get it.
            rewards: Lists and gives the rewards.
            catalogues: Name and draw the items the rewards give, once imported.
            entitlements: Hold what the heroes own, which says what each would get.
            products: Hold the boxes that daily gifts give from.
        """
        self._pages = pages
        self._games = games
        self._rewards = rewards
        self._catalogues = catalogues
        self._entitlements = entitlements
        self._products = products

    def routes(self) -> APIRouter:
        """Return the rewards page of each game that lists it, its claim page and its form."""
        router = APIRouter()
        for game in self._games.with_page("rewards"):
            path = f"/{game.id}"
            router.add_api_route(path + REWARDS, self._page(game), response_class=HTMLResponse)
            router.add_api_route(path + CLAIM, self._claim_page(game), response_class=HTMLResponse)
            router.add_api_route(path + CLAIM, self._claim(game), methods=["POST"])
        return router

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_rewards(request: Request) -> Response:
            cards = await self._cards(game, request)
            words = cards.words
            texts, rules = words.texts, words.rules
            running = await self._rewards.running(game)
            upcoming = [words.event(run) for run in await self._rewards.upcoming(game)]
            player = signed_in(request)
            bar = await site_bar(request)
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
            if bar and bar.roster and player:
                available = bar.rewards
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
                ready=bar.ready if bar else 0,
                daily=words.daily(running),
                year=str(today(words.now, rules.zone).year),
                next=localize(f"/{game.id}{REWARDS}", texts.language),
                reveal=request.session.pop(REVEAL, None),
                paused=rules.paused,
                csrf=form_token(request) if player else "",
            )

        return show_rewards

    def _claim_page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_claim(request: Request) -> Response:
            # The claim form as a page of its own, where scripts show it as a dialog.
            cards = await self._cards(game, request)
            bar = await site_bar(request)
            fields = request.query_params
            key, faction = fields.get("key", ""), fields.get("faction", "")
            reward = _named(bar.rewards if bar else [], key, faction)
            page = localize(f"/{game.id}{REWARDS}", request.state.language)
            if reward is None:
                return RedirectResponse(page, 303)
            return self._pages.render(
                request,
                "rewards.Claim",
                game=game,
                claiming=await cards.claims.claiming(reward),
                back=page,
                csrf=form_token(request),
            )

        return show_claim

    def _claim(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def claim(request: Request) -> Response:
            form = await request.form()
            page = localize(f"/{game.id}{REWARDS}", request.state.language)
            player = signed_in(request)
            if player is None:
                return RedirectResponse(page, 303)
            if not form_matches(request, form):
                toast(request, "web.rewards.refused.form", error=True)
                return RedirectResponse(page, 303)
            cards = await self._cards(game, request)
            claims, words = cards.claims, cards.words
            bar = await site_bar(request)
            key, faction = str(form.get("key", "")), str(form.get("faction", ""))
            reward = _named(bar.rewards if bar else [], key, faction)
            if reward is None:
                message, values = claims.refused(key, None, None, "")
                toast(request, message, error=True, **values)
                return RedirectResponse(page, 303)
            claiming = await claims.claiming(reward)
            chosen = str(form.get("hero", ""))
            hero = claiming.hero or words.hero(int(chosen) if chosen.isdecimal() else None)
            hero_id = hero.persona.id if hero and reward.gift.for_hero else None
            name = hero.persona.name if hero else ""
            try:
                given = await self._rewards.claim(
                    game, player.account_id, reward.key, reward.faction, hero_id
                )
            except RewardRefused as refused:
                # As it stands now, with the claim that was made meanwhile.
                now = await self._rewards.available(game, player.account_id)
                message, values = claims.refused(
                    key, _named(now, key, faction), refused.refusal, name
                )
                toast(request, message, error=True, **values)
                return RedirectResponse(page, 303)
            parts = claiming.extra
            if given.items and hero:
                items = ", ".join(words.item(*item) for item in given.items)
                parts = [words.texts.text("web.rewards.given", items=items, hero=name), *parts]
                if reveal := await claims.reveal(hero, reward.gift, given):
                    request.session[REVEAL] = reveal
            toast(
                request,
                "web.rewards.done.listed" if given.items else "web.rewards.done",
                reward=claiming.title,
                given="; ".join(parts),
                game=game.name,
                funds=str(claiming.funds),
            )
            return RedirectResponse(page, 303)

        return claim

    async def _cards(self, game: Game, request: Request) -> RewardCards:
        rules = await self._rewards.rules(game)
        assert rules is not None
        stores = self._catalogues, self._entitlements, self._products
        art = self._pages.shows_art(request, game)
        return await reward_cards(request, game, rules, *stores, art)


def _named(rewards: list[Reward], key: str, faction: str) -> Reward | None:
    # The reward that a form or address names by its key and its faction, empty for none.
    return next((r for r in rewards if (r.key, r.faction or "") == (key, faction)), None)
