import asyncio
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from openfesl.core.account_rules import account_rules
from openfesl.core.calendar import day_start, today
from openfesl.core.catalogues import Catalogues, Contents
from openfesl.core.chests import key_tag
from openfesl.core.drops import Drops
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game
from openfesl.core.hero_items import fits
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.inventory import Ownership
from openfesl.core.overrides import overridden
from openfesl.core.policies import SettingStore
from openfesl.core.reward_claims import Claim, Claimed, Given, Grant, RewardStore
from openfesl.core.reward_rules import Gift, Goal, Milestone, Part, RewardRules
from openfesl.core.reward_runs import Run, run_of, running_at
from openfesl.core.stats import (
    PERSONA,
    Owner,
    StatStore,
    bucket_of,
    partition_of,
)
from openfesl.core.store import ProductStore
from openfesl.core.wallets import Wallet
from openfesl.protocol.rank import Period as StatPeriod

# The key of the reward that a first treasure chest brings.
FIRST_CHEST = "first-chest"
# How often, in seconds, a day on which claims are paused is looked for, also with nobody on the
# site; a pause is recorded within this long of its day's start.
PAUSE_CHECK_INTERVAL = 60.0

logger = logging.getLogger(__name__)


class RewardState(StrEnum):
    """Whether an account may claim a reward now."""

    CLAIMABLE = "claimable"
    CLAIMED = "claimed"
    LOCKED = "locked"
    """It needs a hero, or a goal reached, that the account lacks."""
    EXPIRED = "expired"
    """Its time to be claimed ran out a short while ago; it is shown until the day after."""


class RewardRefusal(StrEnum):
    """Why a claim gave nothing."""

    PAUSED = "paused"
    UNKNOWN = "unknown"
    """No such reward runs now: it ended, or never was."""
    CLAIMED = "claimed"
    LOCKED = "locked"
    """The account lacks the hero or the goal that the reward needs."""
    UNFIT = "unfit"
    """The hero chosen is not one the reward may go to."""
    EXPIRED = "expired"
    """Its time to be claimed ran out."""


class RewardRefused(Exception):
    """Raised for a claim that gave nothing; nothing was granted."""

    def __init__(self, refusal: RewardRefusal) -> None:
        """Say why the claim gave nothing.

        Args:
            refusal: Why.
        """
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class Reward:
    """A reward as an account sees it now.

    Attributes:
        key: What the account claims it by, unique for each faction, such as an event, its
            year and the day of a daily gift.
        faction: The faction whose heroes it is for; the account's own when none.
        name: The id of its name in the game's texts or the server's own.
        description: The id of the text about it, likewise.
        gift: What it gives.
        state: Whether the account may claim it now.
        heroes: The account's heroes that its items may go to, by persona.
        ends: When it can no longer be claimed; never when none.
        day: The day of its daily event, from 1; none for anything else.
        hero: The persona of the one hero it is for, such as a new hero's starter pack; none for
            any.
        progress: How far towards its goal the account or its hero is, for a goal.
        goal: What it needs reached, for a goal.
        claim: The account's claim of it, once claimed, whenever that was: a milestone is
            claimed once, in whichever year its goal is reached.
        event: The key of the event that brings it; none for a reward that no event brings.
        kept: Whether it is the daily gift of an earlier day, on which claims were paused, so
            that it waits until its event ends.
    """

    key: str
    faction: str | None
    name: str
    description: str | None
    gift: Gift
    state: RewardState
    heroes: tuple[int, ...] = ()
    ends: datetime | None = None
    day: int | None = None
    hero: int | None = None
    progress: int | None = None
    goal: int | None = None
    claim: Claim | None = None
    event: str | None = None
    kept: bool = False


class Rewards:
    """The rewards a game's site gives the accounts that claim them, by its calendar."""

    def __init__(
        self,
        store: RewardStore,
        settings: SettingStore,
        heroes: Heroes,
        stats: StatStore,
        entitlements: EntitlementStore,
        catalogues: Catalogues,
        products: ProductStore,
        drops: Drops,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Give rewards from what stores keep.

        Args:
            store: Where claims are recorded and their rewards given.
            settings: The instance's settings, which override the rewards' game data.
            heroes: The accounts' heroes, with their factions, classes and levels.
            stats: Where the heroes' stats are kept, which goals count.
            entitlements: Where the heroes' items are kept, their treasure chests among them.
            catalogues: The games' imported catalogues, which tell what fits a hero.
            products: Where the store's boxes are kept, whose drops rewards give.
            drops: Picks what a box gives a hero.
            clock: Tells the time; the system's own when none.
        """
        self._store = store
        self._settings = settings
        self._heroes = heroes
        self._stats = stats
        self._entitlements = entitlements
        self._catalogues = catalogues
        self._products = products
        self._drops = drops
        self._clock = clock or (lambda: datetime.now(UTC))

    async def rules(self, game: Game) -> RewardRules | None:
        """Return how a game gives rewards, with the instance's settings over its data.

        Args:
            game: The game.
        """
        if game.rewards is None:
            return None
        prefix = f"{game.id}.rewards."
        return overridden(game.rewards, prefix, await self._settings.settings(prefix))

    async def available(self, game: Game, account_id: int) -> list[Reward]:
        """Return the rewards an account sees now, claimed ones and those it lacks a hero for.

        A day on which claims are paused is recorded the first time an account sees them so,
        and its daily gifts wait until their event ends; once claimed, they are left out.

        Args:
            game: The game.
            account_id: The account.
        """
        rules = await self.rules(game)
        if rules is None:
            return []
        now = self._clock()
        if rules.paused:
            await self._store.pause(game.id, today(now, rules.zone))
        heroes = (await self._heroes.roster(game, account_id)).heroes
        found = [
            *await self._events(game, rules, heroes, now),
            *self._starters(game, rules, heroes, now),
            *await self._milestones(game, rules, heroes),
            *await self._first_chest(game, rules, heroes),
        ]
        claimed = await self._store.claimed(game.id, account_id, {r.key for r in found})
        claims = {(claim.key, claim.faction): claim for claim in claimed}
        return [
            replace(reward, state=RewardState.CLAIMED, claim=claims[reward.key, reward.faction])
            if (reward.key, reward.faction) in claims
            else reward
            for reward in found
            if not (reward.kept and (reward.key, reward.faction) in claims)
        ]

    async def follow_pauses(self, games: Iterable[Game]) -> None:
        """Record each day on which claims of the games' rewards are paused, until cancelled.

        A day is recorded also when no account looks at the rewards, so that the daily gifts of
        every paused day wait until their event ends.

        Args:
            games: The games, of which those with rewards are followed.
        """
        while True:
            try:
                for game in games:
                    rules = await self.rules(game)
                    if rules is not None and rules.paused:
                        await self._store.pause(game.id, today(self._clock(), rules.zone))
            except Exception:
                # The database may be away for a moment; the next check tries again.
                logger.exception("Could not record the days on which claims are paused")
            await asyncio.sleep(PAUSE_CHECK_INTERVAL)

    async def running(self, game: Game) -> list[Run]:
        """Return the events of a game's calendar that run now.

        Args:
            game: The game.
        """
        rules = await self.rules(game)
        return running_at(rules, self._clock()) if rules else []

    async def upcoming(self, game: Game) -> list[Run]:
        """Return the events of a game's calendar that start later, the soonest first.

        Args:
            game: The game.
        """
        rules = await self.rules(game)
        if rules is None:
            return []
        now = self._clock()
        year = today(now, rules.zone).year
        found = []
        for key, event in rules.events.items():
            if event.enabled:
                runs = (run_of(key, event, year + ahead, rules.zone) for ahead in (0, 1, 2))
                found.append(next(run for run in runs if run.period.start > now))
        return sorted(found, key=lambda run: run.period.start)

    async def history(self, game: Game, account_id: int) -> list[Claimed]:
        """Return the rewards an account claimed this year, the latest first, with their names.

        Args:
            game: The game.
            account_id: The account.
        """
        rules = await self.rules(game)
        if rules is None:
            return []
        year = date(today(self._clock(), rules.zone).year, 1, 1)
        claims = await self._store.claims(game.id, account_id, day_start(year, rules.zone))
        return [claimed(rules, claim) for claim in claims]

    async def claim(
        self, game: Game, account_id: int, key: str, faction: str | None, hero_id: int | None
    ) -> Given:
        """Claim a reward for an account, its items going to one of its heroes.

        Everything is checked again at the moment of the claim, and the reward is given at
        once: items to the hero, Funds and keys to the account.

        Args:
            game: The game.
            account_id: The account.
            key: What the reward is claimed by.
            faction: The faction of its part; none for the account's own.
            hero_id: The persona of the hero its items go to; none for a reward without items.

        Returns:
            What the hero got.

        Raises:
            RewardRefused: When claims are paused, no such reward runs now, the account claimed
                it already, its time ran out, or the hero is not one it may go to or may get
                nothing of it.
        """
        rules = await self.rules(game)
        if rules is not None and rules.paused:
            raise RewardRefused(RewardRefusal.PAUSED)
        rewards = await self.available(game, account_id) if rules else []
        reward = next((r for r in rewards if (r.key, r.faction) == (key, faction)), None)
        if rules is None or reward is None:
            raise RewardRefused(RewardRefusal.UNKNOWN)
        if reward.state is not RewardState.CLAIMABLE:
            raise RewardRefused(RewardRefusal(reward.state.value))
        gift = reward.gift
        if gift.for_hero != (hero_id is not None) or (hero_id and hero_id not in reward.heroes):
            raise RewardRefused(RewardRefusal.UNFIT)
        items, rolled = (
            await self._items(game, account_id, gift, hero_id) if hero_id else ((), False)
        )
        funds = None
        if gift.funds:
            currencies = (await account_rules(game, self._settings)).currencies
            allowance = currencies[rules.currency].allowance
            funds = Wallet(game.id, rules.currency, account_id, None, allowance), gift.funds
        keys = (key_tag(game.chests), gift.keys) if gift.keys and game.chests else None
        grant = Grant(
            game.id,
            account_id,
            key,
            faction,
            hero_id,
            items,
            rules.fallback,
            funds,
            keys,
            self._clock(),
        )
        return Given(await self._store.grant(grant), rolled)

    async def _events(
        self, game: Game, rules: RewardRules, heroes: list[Hero], now: datetime
    ) -> list[Reward]:
        runs = running_at(rules, now)
        firsts = [today(run.period.start, rules.zone) for run in runs if run.event.daily]
        paused = await self._store.paused(game.id, min(firsts)) if firsts else set()
        found = []
        for key, event, part, ends, day, kept in _parts(rules, now, runs, paused):
            gift, goal = part.on(day), part.goal
            counting = [h for h in heroes if not goal or goal.hero_class in (None, h.hero_class)]
            fitting = tuple(
                h.persona.id for h in counting if self._fits(game, part.faction, gift, h)
            )
            # The event's own text names its parts by the event's title.
            name = rules.events[event].title if rules.events[event].own_text else part.name
            description = _dated(part.description, day) if part.description else None
            reward = Reward(
                key,
                part.faction,
                _dated(name, day),
                description,
                gift,
                _state(not gift.for_hero or bool(fitting)),
                fitting,
                ends,
                day,
                event=event,
                kept=kept,
            )
            if goal:
                hero, progress = await self._best(game, key, goal, part.faction, counting)
                reached = bool(fitting) and progress >= goal.count
                reward = replace(
                    reward, state=_state(reached), hero=hero, progress=progress, goal=goal.count
                )
            found.append(reward)
        return found

    async def _best(
        self, game: Game, key: str, goal: Goal, faction: str | None, heroes: list[Hero]
    ) -> tuple[int | None, int]:
        # The hero of the faction that came furthest since the event began, and how far.
        counted = [hero.persona.id for hero in heroes if hero.faction == faction]
        bucket = bucket_of(StatPeriod.ALL_TIME, 0)
        partition = partition_of(game)
        before = await self._store.baselines(game.id, partition, key, goal.stat, bucket, counted)
        owners = [Owner(PERSONA, persona) for persona in counted]
        now = await self._stats.values(partition, owners, [goal.stat], bucket)
        progress = {
            owner.id: int(now[(owner, goal.stat)].value) - before.get(owner.id, 0)
            for owner in owners
            if (owner, goal.stat) in now
        }
        if not progress:
            return None, 0
        best = max(progress, key=progress.__getitem__)
        return best, max(progress[best], 0)

    def _starters(
        self, game: Game, rules: RewardRules, heroes: list[Hero], now: datetime
    ) -> list[Reward]:
        packs = rules.starter
        if packs is None or not packs.enabled:
            return []
        found = []
        for hero in heroes:
            gift = packs.packs.get(hero.faction or "", {}).get(hero.hero_class or "")
            # Claimable to the end of its last day, then shown as expired for a day more.
            last = packs.last_day(hero.created, rules.zone)
            ends = day_start(last + timedelta(1), rules.zone)
            if gift is None or now >= day_start(last + timedelta(2), rules.zone):
                continue
            fits_hero = self._fits(game, hero.faction, gift, hero)
            found.append(
                Reward(
                    f"starter:{hero.persona.id}",
                    hero.faction,
                    packs.name,
                    packs.description,
                    gift,
                    RewardState.EXPIRED if now >= ends else _state(fits_hero),
                    (hero.persona.id,) if fits_hero else (),
                    ends,
                    hero=hero.persona.id,
                )
            )
        return found

    async def _milestones(self, game: Game, rules: RewardRules, heroes: list[Hero]) -> list[Reward]:
        shown = {key: goal for key, goal in rules.milestones.items() if goal.enabled}
        keys = sorted({stat for goal in shown.values() for stat in goal.stats})
        owners = [Owner(PERSONA, hero.persona.id) for hero in heroes]
        bucket = bucket_of(StatPeriod.ALL_TIME, 0)
        values = await self._stats.values(partition_of(game), owners, keys, bucket)

        def count(goal: Milestone, hero: Hero) -> int:
            owner = Owner(PERSONA, hero.persona.id)
            return sum(int(values[(owner, s)].value) for s in goal.stats if (owner, s) in values)

        found = []
        for key, goal in shown.items():
            counted: list[tuple[Hero | None, int]] = [(h, count(goal, h)) for h in heroes]
            if not goal.each:
                total = sum(value for _, value in counted) if goal.stats else len(heroes)
                counted = [(None, total)]
            found += [
                Reward(
                    f"milestone:{key}" + (f":{hero.persona.id}" if hero else ""),
                    None,
                    goal.name,
                    goal.description,
                    goal.gift,
                    _state(value >= goal.goal),
                    hero=hero.persona.id if hero else None,
                    progress=value,
                    goal=goal.goal,
                )
                for hero, value in counted
            ]
        return found

    async def _first_chest(
        self, game: Game, rules: RewardRules, heroes: list[Hero]
    ) -> list[Reward]:
        reward, chests = rules.first_chest, game.chests
        if reward is None or not reward.enabled or chests is None:
            return []
        arrived = [await self._entitlements.of_persona(h.persona.id, chests.tag) for h in heroes]
        state = _state(any(arrived))
        return [Reward(FIRST_CHEST, None, reward.name, reward.description, reward.gift, state)]

    def _fits(self, game: Game, faction: str | None, gift: Gift, hero: Hero) -> bool:
        # A hero of the faction, whom the gift's items fit or who gets a drop of its box.
        contents = self._catalogues.contents(game)
        return not gift.for_hero or (
            hero.faction == faction
            and hero.hero_class is not None
            and (bool(gift.box) or _usable(contents, gift, hero))
        )

    async def _items(
        self, game: Game, account_id: int, gift: Gift, hero_id: int
    ) -> tuple[tuple[tuple[int, Ownership], ...], bool]:
        # The gift's items, which fit the hero as its claim checked, or a roll of its box, and
        # whether the box was rolled.
        roster = await self._heroes.roster(game, account_id)
        hero = next(hero for hero in roster.heroes if hero.persona.id == hero_id)
        if not gives_box(self._catalogues.contents(game), gift, hero):
            return gift.items, False
        box = await self._products.box_by_key(game.id, gift.box) if gift.box else None
        won = await self._drops.roll(game, box, hero.persona, hero) if box else None
        if won is None:
            raise RewardRefused(RewardRefusal.UNFIT)
        return ((won.item_id, won.ownership),), True


def claimed(rules: RewardRules, claim: Claim) -> Claimed:
    """Return a claim with what names it: the reward that its key stands for.

    Args:
        rules: The rewards, which name the reward.
        claim: The claim.
    """
    # A milestone's name states its goal.
    kind, _, rest = claim.key.partition(":")
    name, goal = claim.key, None
    if kind == "milestone" and (milestone := rules.milestones.get(rest.partition(":")[0])):
        name, goal = milestone.name, milestone.goal
    elif kind == "starter" and rules.starter:
        name = rules.starter.name
    elif kind == FIRST_CHEST and rules.first_chest:
        name = rules.first_chest.name
    elif (event := rules.events.get(kind)) and event.own_text:
        name = event.title
    elif event:
        parts = (part for part in event.parts.values() if part.faction == claim.faction)
        if part := next(parts, None):
            day = rest.partition(":")[2]
            name = _dated(part.name, int(day) if day else None)
    return Claimed(claim, name, goal)


def gives_box(contents: Contents, gift: Gift, hero: Hero) -> bool:
    """Return whether a gift gives a hero one item of its box, its own items not fitting it.

    A gift without items of its own always gives from its box, if it has one.

    Args:
        contents: The game's imported catalogue, which tells what fits a hero.
        gift: The gift.
        hero: The hero.
    """
    return bool(gift.box) and not (gift.items and _usable(contents, gift, hero))


def _usable(contents: Contents, gift: Gift, hero: Hero) -> bool:
    # Whether the hero may use each of the gift's items.
    found = [contents.items.get(item_id) for item_id, _ in gift.items]
    return all(
        item is not None and fits(item, {item.kind}, hero.faction, hero.hero_class, hero.level)
        for item in found
    )


def _parts(
    rules: RewardRules, now: datetime, runs: list[Run], paused: set[date]
) -> list[tuple[str, str, Part, datetime, int | None, bool]]:
    # Each part of the events that run, with its key, its event's, when it can no longer be
    # claimed, its day and whether that day is kept: a daily gift is claimed on its day alone,
    # or until its event ends where claims were paused on its day.
    found = []
    day = today(now, rules.zone)
    for run in runs:
        parts = [part for part in run.event.parts.values() if part.enabled]
        days: list[tuple[int | None, datetime, bool]] = [(None, run.period.end, False)]
        if run.event.daily:
            first = today(run.period.start, rules.zone)
            days = [(run.day, day_start(day + timedelta(1), rules.zone), False)]
            days += [
                ((kept - first).days + 1, run.period.end, True)
                for kept in sorted(paused)
                if first <= kept < day
            ]
        found += [
            (
                f"{run.key}:{run.year}" + (f":{number}" if number else ""),
                run.key,
                part,
                ends,
                number,
                kept,
            )
            for number, ends, kept in days
            for part in parts
        ]
    return found


def _state(may: bool) -> RewardState:
    return RewardState.CLAIMABLE if may else RewardState.LOCKED


def _dated(text: str, day: int | None) -> str:
    return text.replace("{day}", str(day)) if day else text
