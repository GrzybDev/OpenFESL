from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

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
from openfesl.core.reward_claims import Grant, RewardStore
from openfesl.core.reward_rules import Gift, Goal, Milestone, Part, RewardRules
from openfesl.core.stats import (
    PERSONA,
    Owner,
    StatStore,
    bucket_of,
    partition_of,
)
from openfesl.core.store import ProductStore
from openfesl.core.wallets import Wallet
from openfesl.protocol.rank import Period


class RewardState(StrEnum):
    """Whether an account may claim a reward now."""

    CLAIMABLE = "claimable"
    CLAIMED = "claimed"
    LOCKED = "locked"
    """It needs a hero, or a goal reached, that the account lacks."""


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

        Args:
            game: The game.
            account_id: The account.
        """
        rules = await self.rules(game)
        if rules is None:
            return []
        now = self._clock()
        heroes = (await self._heroes.roster(game, account_id)).heroes
        found = [
            *await self._events(game, rules, heroes, now),
            *self._starters(game, rules, heroes, now),
            *await self._milestones(game, rules, heroes),
            *await self._first_chest(game, rules, heroes),
        ]
        claimed = await self._store.claimed(game.id, account_id, {r.key for r in found})
        return [
            replace(reward, state=RewardState.CLAIMED)
            if (reward.key, reward.faction) in claimed
            else reward
            for reward in found
        ]

    async def claim(
        self, game: Game, account_id: int, key: str, faction: str | None, hero_id: int | None
    ) -> list[int]:
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
            The items the hero got.

        Raises:
            RewardRefused: When claims are paused, no such reward runs now, the account claimed
                it already, or the hero is not one it may go to or may get nothing of it.
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
        items = await self._items(game, account_id, gift, hero_id) if hero_id else ()
        funds = None
        if gift.funds:
            allowance = game.currencies[rules.currency].allowance
            funds = Wallet(game.id, rules.currency, account_id, None, allowance), gift.funds
        keys = (key_tag(game.chests), gift.keys) if gift.keys and game.chests else None
        grant = Grant(
            game.id, account_id, key, faction, hero_id, items, rules.fallback, funds, keys
        )
        return await self._store.grant(grant)

    async def _events(
        self, game: Game, rules: RewardRules, heroes: list[Hero], now: datetime
    ) -> list[Reward]:
        found = []
        for key, part, ends, day in _running(rules, now):
            gift, goal = part.on(day), part.goal
            counting = [h for h in heroes if not goal or goal.hero_class in (None, h.hero_class)]
            fitting = tuple(
                h.persona.id for h in counting if self._fits(game, part.faction, gift, h)
            )
            description = _dated(part.description, day) if part.description else None
            reward = Reward(
                key,
                part.faction,
                _dated(part.name, day),
                description,
                gift,
                _state(not gift.for_hero or bool(fitting)),
                fitting,
                ends,
                day,
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
        bucket = bucket_of(Period.ALL_TIME, 0)
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
            ends = hero.created + timedelta(days=packs.days)
            if gift is None or now >= ends:
                continue
            fits_hero = self._fits(game, hero.faction, gift, hero)
            found.append(
                Reward(
                    f"starter:{hero.persona.id}",
                    hero.faction,
                    packs.name,
                    packs.description,
                    gift,
                    _state(fits_hero),
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
        bucket = bucket_of(Period.ALL_TIME, 0)
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
        return [Reward("first-chest", None, reward.name, reward.description, reward.gift, state)]

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
    ) -> tuple[tuple[int, Ownership], ...]:
        roster = await self._heroes.roster(game, account_id)
        hero = next(hero for hero in roster.heroes if hero.persona.id == hero_id)
        if gift.items and _usable(self._catalogues.contents(game), gift, hero):
            return gift.items
        box = await self._products.box_by_key(game.id, gift.box) if gift.box else None
        won = await self._drops.roll(game, box, hero.persona, hero) if box else None
        if won is None:
            raise RewardRefused(RewardRefusal.UNFIT)
        return ((won.item_id, won.ownership),)


def _usable(contents: Contents, gift: Gift, hero: Hero) -> bool:
    # Whether the hero may use each of the gift's items.
    found = [contents.items.get(item_id) for item_id, _ in gift.items]
    return all(
        item is not None and fits(item, {item.kind}, hero.faction, hero.hero_class, hero.level)
        for item in found
    )


def _running(rules: RewardRules, now: datetime) -> list[tuple[str, Part, datetime, int | None]]:
    # Each part of the events that run, with its key, when it can no longer be claimed and its
    # day; a daily gift is claimed on its day alone.
    found = []
    for name, event in rules.events.items():
        period = event.window.running(now, rules.zone) if event.enabled else None
        if period is None:
            continue
        day = today(now, rules.zone)
        number = event.window.day(now, rules.zone) if event.daily else None
        key = f"{name}:{day.year}" + (f":{number}" if number else "")
        ends = day_start(day + timedelta(1), rules.zone) if number else period.end
        found += [(key, part, ends, number) for part in event.parts.values() if part.enabled]
    return found


def _state(may: bool) -> RewardState:
    return RewardState.CLAIMABLE if may else RewardState.LOCKED


def _dated(text: str, day: int | None) -> str:
    return text.replace("{day}", str(day)) if day else text
