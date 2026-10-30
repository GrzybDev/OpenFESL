from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from babel import Locale
from babel.dates import format_interval, format_skeleton, format_timedelta
from starlette.requests import Request

from openfesl.core.calendar import day_start, today
from openfesl.core.catalogues import Catalogue, Catalogues, game_text
from openfesl.core.games import Game
from openfesl.core.heroes import Hero, Roster
from openfesl.core.inventory import Ownership
from openfesl.core.reward_claims import Claim, Claimed
from openfesl.core.reward_rules import Gift, Milestone, RewardRules
from openfesl.core.reward_runs import Run
from openfesl.core.rewards import Reward
from openfesl.core.texts import translate
from openfesl.web.catalogue_files import icon_url
from openfesl.web.site import site_bar
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class EventText:
    """An event of the rewards calendar as pages name it.

    Attributes:
        name: Its name.
        dates: Its first and last days, such as 1 to 24 Dec.
        start: Its first day.
        summary: What it gives, in a sentence.
    """

    name: str
    dates: str
    start: str
    summary: str


@dataclass(frozen=True, slots=True)
class Past:
    """A reward of this year in the list of claims.

    Attributes:
        day: When it was claimed, or ran out unclaimed.
        name: Its name.
        given: What it gave and to whom; empty for one that ran out unclaimed.
    """

    day: str
    name: str
    given: str


class RewardTexts:
    """What pages say of a game's rewards and claims, in their language and the rewards' zone."""

    def __init__(
        self,
        game: Game,
        rules: RewardRules,
        texts: StoreTexts,
        catalogue: Catalogue,
        roster: Roster | None,
        art: bool,
    ) -> None:
        """Describe a game's rewards to an account, or to a visitor signed out.

        Args:
            game: The game.
            rules: How the game gives rewards, with the instance's settings.
            texts: Writes the page's language.
            catalogue: The game's imported catalogue, which names and draws the items.
            roster: The account's heroes; none for a visitor signed out.
            art: Whether the page shows the game's art to its viewer, its items' icons among it.
        """
        self.game = game
        self.rules = rules
        self.texts = texts
        self.catalogue = catalogue
        self.roster = roster
        self.art = art
        self.locale = Locale.parse(translate("web.locale", texts.language))
        self.zone = ZoneInfo(rules.zone)
        self.now = datetime.now(UTC)

    def hero(self, persona_id: int | None) -> Hero | None:
        """Return one of the account's heroes, if it has one by a persona.

        Args:
            persona_id: The hero's persona.
        """
        heroes = self.roster.heroes if self.roster else []
        return next((hero for hero in heroes if hero.persona.id == persona_id), None)

    def day(self, value: datetime, skeleton: str = "MMMd") -> str:
        """Return the day of a moment in the rewards' zone, such as 28 Oct.

        Args:
            value: The moment.
            skeleton: Which parts of the day to write, as Babel's skeletons name them, such as
                yMMMd for the day with its year.
        """
        return str(format_skeleton(skeleton, value, tzinfo=self.zone, locale=self.locale))

    def dated(self, day: date) -> str:
        """Return a day of the rewards' zone, such as 28 Oct.

        Args:
            day: The day.
        """
        return self.day(day_start(day, self.rules.zone))

    def last_day(self, reward: Reward) -> date:
        """Return the last day on which a reward may be claimed, in the rewards' zone.

        Args:
            reward: The reward, which ends.
        """
        assert reward.ends
        return today(reward.ends, self.rules.zone) - timedelta(days=1)

    def clock(self, value: datetime) -> str:
        """Return the time of a moment in the rewards' zone with the zone, such as 00:00 UTC.

        Args:
            value: The moment.
        """
        shown = format_skeleton("Hm", value, tzinfo=self.zone, locale=self.locale)
        return f"{shown} {self.rules.zone}"

    def funds(self, value: int) -> str:
        """Return an amount of the rewards' Funds with the currency's name.

        Args:
            value: The amount.
        """
        return self.texts.priced(value, self.rules.currency)

    def item(self, item_id: int, ownership: Ownership) -> str:
        """Return an item's name and how a hero owns it, such as M95 · 7 days.

        Args:
            item_id: The item's number.
            ownership: How the hero owns it.
        """
        name = self.catalogue.name(item_id) or str(item_id)
        term = self.texts.ownership(ownership, inline=True)
        return self.texts.text("web.deals.card.item", item=name, ownership=term)

    def icon(self, item_id: int) -> str | None:
        """Return the address of an item's imported icon, where the page shows the game's art.

        Args:
            item_id: The item's number.
        """
        return icon_url(self.game.id, self.catalogue, item_id, self.art)

    def event(self, run: Run) -> EventText:
        """Return an event's name, its dates, its first day and what it gives.

        Args:
            run: The event in the year it runs or runs next.
        """
        last = run.period.end - timedelta(days=1)
        dates = format_interval(
            run.period.start, last, "MMMd", tzinfo=self.zone, locale=self.locale
        )
        texts = self.texts
        return EventText(
            texts.text(run.event.title),
            str(dates),
            self.day(run.period.start),
            texts.text(run.event.summary),
        )

    def title(self, reward: Reward) -> str:
        """Return a reward's name: the game's text, the server's own or its event's.

        Args:
            reward: The reward.
        """
        kind, _, rest = reward.key.partition(":")
        if kind == "milestone":
            return self.milestone_title(self.rules.milestones[rest.partition(":")[0]])
        found = game_text(self.game, self.catalogue, reward.name, self.texts.language)
        if found == reward.name and reward.event:
            # Before the game's texts are imported, the event's own name stands in.
            return self.texts.text(self.rules.events[reward.event].title)
        return found

    def milestone_title(self, milestone: Milestone) -> str:
        """Return a milestone's name, which states its goal, and the Funds it gives.

        Args:
            milestone: The milestone.
        """
        name = self.texts.text(milestone.name).format(goal=self.texts.amount(milestone.goal))
        funds = self.funds(milestone.gift.funds)
        return self.texts.text("web.rewards.milestone.title", name=name, amount=funds)

    def band(self, reward: Reward) -> str:
        """Return whom a reward is for: its one hero, its faction or the account.

        Args:
            reward: The reward.
        """
        if reward.hero is not None and reward.goal is None and (hero := self.hero(reward.hero)):
            return hero.persona.name
        if reward.faction:
            return self.texts.factions(frozenset({reward.faction}))
        return self.texts.text("web.rewards.band.account")

    def extra(self, gift: Gift) -> list[str]:
        """Return what the account gets of a gift besides a hero's items, a line each.

        Args:
            gift: The gift.
        """
        lines = []
        if gift.funds:
            lines.append(self.texts.text("web.rewards.gift.funds", amount=self.funds(gift.funds)))
        if gift.keys:
            lines.append(self.texts.count("web.rewards.gift.keys", gift.keys))
        return lines

    def given(self, claim: Claim) -> str:
        """Return when a reward was claimed, for which hero and what it gave.

        Args:
            claim: The claim.
        """
        parts = [", ".join(self.item(*item) for item in claim.items)] if claim.items else []
        parts += self.extra(Gift(funds=claim.funds, keys=claim.keys))
        hero = self.hero(claim.hero)
        return self.texts.text(
            "web.rewards.state.claimed.hero" if hero else "web.rewards.state.claimed",
            date=self.day(claim.at),
            hero=hero.persona.name if hero else "",
            given="; ".join(parts),
        )

    def past(self, claimed: Claimed) -> Past:
        """Return a claim as the list of this year's claims shows it.

        Args:
            claimed: The claim.
        """
        name = claimed.name
        if claimed.goal is not None:
            name = self.texts.text(name).format(goal=self.texts.amount(claimed.goal))
        else:
            name = game_text(self.game, self.catalogue, name, self.texts.language)
        return Past(self.day(claimed.claim.at), name, self.given(claimed.claim))

    def lapsed(self, history: list[Claimed]) -> list[Past]:
        """Return this year's starter packs that ran out unclaimed and left the page since.

        Args:
            history: What the account claimed this year.
        """
        packs = self.rules.starter
        if not (packs and packs.enabled and self.roster):
            return []
        now = today(self.now, self.rules.zone)
        claimed = {claim.claim.key for claim in history}
        lapsed = []
        for hero in self.roster.heroes:
            last = packs.last_day(hero.created, self.rules.zone)
            unclaimed = f"starter:{hero.persona.id}" not in claimed
            if unclaimed and last.year == now.year and (now - last).days > 1:
                name = f"{self.texts.text(packs.name)} · {hero.persona.name}"
                lapsed.append(Past(self.dated(last), name, ""))
        return lapsed

    def daily(self, running: list[Run]) -> str:
        """Return when the next daily gifts come and in how long, while a daily event runs.

        Args:
            running: The events that run now.
        """
        if not any(run.event.daily for run in running):
            return ""
        starts = day_start(today(self.now, self.rules.zone) + timedelta(1), self.rules.zone)
        left = format_timedelta(starts - self.now, add_direction=True, locale=self.locale)
        return self.texts.text("web.rewards.next_daily.at", time=self.clock(starts), left=left)


async def reward_texts(
    request: Request, game: Game, rules: RewardRules, catalogues: Catalogues, art: bool
) -> RewardTexts:
    """Return what a page says of a game's rewards to its viewer, in the page's language.

    Args:
        request: The page's request, whose bar holds the signed-in account's heroes.
        game: The game.
        rules: How the game gives rewards, with the instance's settings.
        catalogues: Name and draw the items the rewards give, once imported.
        art: Whether the page shows the game's art to its viewer.
    """
    language = request.state.language
    bar = await site_bar(request)
    return RewardTexts(
        game,
        rules,
        StoreTexts(game, language, datetime.now(UTC)),
        await catalogues.of(game, language),
        bar.roster if bar else None,
        art,
    )
