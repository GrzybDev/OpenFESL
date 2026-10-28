from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from openfesl.core.calendar import day_start, today
from openfesl.core.catalogues import Catalogues, Contents
from openfesl.core.chests import key_tag
from openfesl.core.drops import Drops
from openfesl.core.games import Game
from openfesl.core.hero_items import fits
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.inventory import Ownership
from openfesl.core.overrides import overridden
from openfesl.core.policies import SettingStore
from openfesl.core.reward_claims import Grant, RewardStore
from openfesl.core.reward_rules import Gift, Part, RewardRules
from openfesl.core.store import ProductStore
from openfesl.core.wallets import Wallet


class RewardState(StrEnum):
    """Whether an account may claim a reward now."""

    CLAIMABLE = "claimable"
    CLAIMED = "claimed"
    LOCKED = "locked"
    """It needs a hero the account lacks."""


class RewardRefusal(StrEnum):
    """Why a claim gave nothing."""

    PAUSED = "paused"
    UNKNOWN = "unknown"
    """No such reward runs now: it ended, or never was."""
    CLAIMED = "claimed"
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
        key: What the account claims it by, unique for each faction: the event, the year and
            for a daily gift its day.
        faction: The faction whose heroes it is for; the account's own when none.
        name: The id of its name in the game's texts or the server's own.
        description: The id of the text about it, likewise.
        gift: What it gives.
        state: Whether the account may claim it now.
        heroes: The account's heroes that its items may go to, by persona.
        ends: When it can no longer be claimed.
        day: The day of its daily event, from 1; none for an event of one gift.
    """

    key: str
    faction: str | None
    name: str
    description: str | None
    gift: Gift
    state: RewardState
    heroes: tuple[int, ...]
    ends: datetime
    day: int | None


class Rewards:
    """The rewards a game's site gives the accounts that claim them, by its calendar."""

    def __init__(
        self,
        store: RewardStore,
        settings: SettingStore,
        heroes: Heroes,
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
            catalogues: The games' imported catalogues, which tell what fits a hero.
            products: Where the store's boxes are kept, whose drops rewards give.
            drops: Picks what a box gives a hero.
            clock: Tells the time; the system's own when none.
        """
        self._store = store
        self._settings = settings
        self._heroes = heroes
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
        found = _running(rules, now)
        claimed = await self._store.claimed(game.id, account_id, {key for key, *_ in found})
        rewards = []
        for key, part, ends, day in found:
            gift = part.on(day)
            fitting = tuple(
                hero.persona.id for hero in heroes if self._fits(game, part, gift, hero)
            )
            if (key, part.faction) in claimed:
                state = RewardState.CLAIMED
            elif gift.for_hero and not fitting:
                state = RewardState.LOCKED
            else:
                state = RewardState.CLAIMABLE
            rewards.append(
                Reward(
                    key,
                    part.faction,
                    _dated(part.name, day),
                    _dated(part.description, day) if part.description else None,
                    gift,
                    state,
                    fitting,
                    ends,
                    day,
                )
            )
        return rewards

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
        if reward.state is RewardState.CLAIMED:
            raise RewardRefused(RewardRefusal.CLAIMED)
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

    def _fits(self, game: Game, part: Part, gift: Gift, hero: Hero) -> bool:
        # A hero of the part's faction, whom its items fit or who gets a drop of its box.
        contents = self._catalogues.contents(game)
        return not gift.for_hero or (
            hero.faction == part.faction
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


def _dated(text: str, day: int | None) -> str:
    return text.replace("{day}", str(day)) if day else text
