from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Heroes
from openfesl.core.referrals import Leader
from openfesl.core.reward_rules import ReferralRules
from openfesl.core.rewards import Reward, Rewards, RewardState
from openfesl.web import referral_texts
from openfesl.web.account.session import signed_in
from openfesl.web.pages import Pages
from openfesl.web.reward_cards import gift_glyph
from openfesl.web.reward_texts import reward_texts
from openfesl.web.site import site_bar

PAGE = "/recruit-a-friend"
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


@dataclass(frozen=True, slots=True)
class LinkBox:
    """Recruit a Friend as a box beside a page shows it, while the programme runs.

    Attributes:
        link: The signed-in viewer's personal link; empty for a visitor signed out.
        rules: The programme, whose steps' levels and days the box names.
    """

    link: str
    rules: ReferralRules


@dataclass(frozen=True, slots=True)
class StepView:
    """A step of Recruit a Friend after the sign-up, as the page explains it.

    Attributes:
        number: The number players see it by.
        title: Its goal in a few words.
        goal: Its goal in fewer words, for a column of the recruits' table.
        days: Within how many days of the recruit's sign-up; none for no deadline.
        text: Which heroes count and by when.
        recruiter: What the recruiter gets.
        recruit: What the recruit gets.
        glyphs: The glyphs of the recruiter's and the recruit's gifts.
        icons: The imported icons of the recruiter's and the recruit's gifts, where the page
            shows the game's art and a gift gives an item.
    """

    number: int
    title: str
    goal: str
    days: int | None
    text: str
    recruiter: str
    recruit: str
    glyphs: tuple[str, str]
    icons: tuple[str | None, str | None]


@dataclass(frozen=True, slots=True)
class RecruitRow:
    """A recruit as the recruiter's table lists it.

    Attributes:
        account_id: The recruit's account, whose player profile the name leads to.
        name: Its display name.
        joined: When it signed up, in words.
        leader: Its hero with the highest level; none before its first.
        chips: Where each step stands, in order.
        ready: Whether a step waits to be claimed on the rewards page.
    """

    account_id: int
    name: str
    joined: str
    leader: Leader | None
    chips: list[referral_texts.Chip]
    ready: bool


class ReferralLinks:
    """Recruit a Friend's page, the landing of its personal links, and who a link names.

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
        catalogues: Catalogues,
        accounts: Callable[[int], Awaitable[Account | None]],
        public_url: str | None,
    ) -> None:
        """Prepare the page and the links of each game that lists Recruit a Friend.

        Args:
            pages: Renders the pages.
            games: The games, of which those listing Recruit a Friend get links.
            rewards: Tell whether the programme runs, its steps and the recruits' rewards.
            heroes: Tell the default hero of the account a link names.
            catalogues: Name the items the steps give, once imported.
            accounts: Look up an account by its number.
            public_url: The address the pages are reached at, which the links start with; the
                request's own when None.
        """
        self._pages = pages
        self._games = games.with_page("recruit-a-friend")
        self._rewards = rewards
        self._heroes = heroes
        self._catalogues = catalogues
        self._accounts = accounts
        self._public_url = public_url.rstrip("/") if public_url else None

    def routes(self) -> APIRouter:
        """Return the page of each game's Recruit a Friend and the landing of its links."""
        router = APIRouter()
        for game in self._games:
            path = f"/{game.id}"
            router.add_api_route(path + PAGE, self._page(game), response_class=HTMLResponse)
            router.add_api_route(
                f"{path}{REF}/{{account_id:int}}", self._landing(game), response_class=HTMLResponse
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

    async def box(self, request: Request, game: Game) -> LinkBox | None:
        """Return Recruit a Friend's box for a page of a game while the programme runs there.

        An account signed in without a display name gets none.

        Args:
            request: The page's request, whose viewer's link the box holds when signed in.
            game: The game.
        """
        rules = await self._rewards.rules(game) if game in self._games else None
        if not (rules and rules.referral and rules.referral.enabled):
            return None
        player = signed_in(request)
        # A link of an account without a display name recruits nobody; the page says why.
        if player and not player.name:
            return None
        return LinkBox(
            self._link(request, game, player.account_id) if player else "", rules.referral
        )

    async def _recruiter(self, game: Game, account_id: int) -> Account | None:
        # The account a link names, while the programme runs and others see the account.
        rules = await self._rewards.rules(game)
        if not (rules and rules.referral and rules.referral.enabled):
            return None
        account = await self._accounts(account_id)
        return account if account and account.public else None

    def _link(self, request: Request, game: Game, account_id: int) -> str:
        base = self._public_url or str(request.base_url).rstrip("/")
        return f"{base}/{game.id}{REF}/{account_id}"

    def _page(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_page(request: Request) -> Response:
            rules = await self._rewards.rules(game)
            assert rules is not None and rules.referral is not None
            player = signed_in(request)
            bar = await site_bar(request)
            art = self._pages.shows_art(request, game)
            words = await reward_texts(request, game, rules, self._catalogues, art)
            texts = words.texts
            steps = []
            for name, step in rules.referral.steps.items():
                window = ".window" if step.days else ""
                values = {"level": step.level, "days": step.days or 0}
                gets = words.referral_name(name, False)
                if step.recruit.gift.for_hero:
                    gets = texts.text("web.referral.item_on_hero", item=gets, level=step.level)
                steps.append(
                    StepView(
                        referral_texts.NUMBERS[name],
                        texts.text(f"web.referral.step.level{window}", **values),
                        texts.text(f"web.referral.goal{window}", **values),
                        step.days,
                        texts.text(f"web.referral.step.any_hero{window}", **values),
                        words.referral_name(name, True),
                        gets,
                        (gift_glyph(step.recruiter.gift), gift_glyph(step.recruit.gift)),
                        (words.gift_icon(step.recruiter.gift), words.gift_icon(step.recruit.gift)),
                    )
                )
            # The steps of each public recruit, as the rewards list them, newest recruit first.
            grouped: dict[int, list[Reward]] = {}
            for reward in bar.rewards if bar and player else []:
                if reward.referral and reward.referral.recruiter:
                    grouped.setdefault(reward.referral.recruit.account.id, []).append(reward)
            rows = []
            for found in grouped.values():
                referral = found[0].referral
                assert referral is not None
                recruit = referral.recruit
                rows.append(
                    RecruitRow(
                        recruit.account.id,
                        recruit.account.display_name or "",
                        words.day(recruit.joined, "yMMMd"),
                        recruit.leader,
                        [referral_texts.chip(words, reward) for reward in found],
                        any(reward.state is RewardState.CLAIMABLE for reward in found),
                    )
                )
            return self._pages.render(
                request,
                "referral.Recruit",
                game=game,
                running=rules.referral.enabled,
                steps=steps,
                link=self._link(request, game, player.account_id) if player and player.name else "",
                recruits=rows,
                hero=bar.roster.playing if bar and bar.roster else None,
            )

        return show_page

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
