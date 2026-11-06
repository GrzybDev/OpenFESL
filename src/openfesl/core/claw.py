from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from fractions import Fraction
from typing import Protocol

from openfesl.core.account_rules import account_rules
from openfesl.core.calendar import day_start, today
from openfesl.core.catalogues import Catalogues
from openfesl.core.claw_rules import CLAW, ClawRules
from openfesl.core.drops import Box, Drops
from openfesl.core.games import Game
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.inventory import Held, Ownership
from openfesl.core.overrides import with_settings
from openfesl.core.personas import Persona
from openfesl.core.policies import SettingStore
from openfesl.core.rewards import Rewards
from openfesl.core.store import Product, ProductStore
from openfesl.core.wallets import Wallet, account_wallet


class ClawRefusal(StrEnum):
    """Why a grab gave nothing.

    The Claw is off or has no prizes, the day's free grabs are used, paid grabs are off, the
    hero is not one of the account's, the account is short of the price, the hero may get no
    prize, or it came to own the prize for good meanwhile.
    """

    OFF = "off"
    USED = "used"
    PAID_OFF = "paid-off"
    UNFIT = "unfit"
    FUNDS = "funds"
    NOTHING = "nothing"
    OWNED = "owned"


class ClawRefused(Exception):
    """Raised for a grab that gave nothing; nothing was taken or given."""

    def __init__(self, refusal: ClawRefusal) -> None:
        """Say why the grab gave nothing.

        Args:
            refusal: Why.
        """
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class Grab:
    """A grab as the Claw keeps it.

    Attributes:
        hero_id: The persona of the hero the prize went to.
        hero: The hero's name.
        item_id: The prize's item.
        ownership: How the hero got it.
        rare: Whether it was a rare prize.
        paid: Whether the grab was bought.
        at: When it was made.
    """

    hero_id: int
    hero: str
    item_id: int
    ownership: Ownership
    rare: bool
    paid: bool
    at: datetime


@dataclass(frozen=True, slots=True)
class NewGrab:
    """A grab to record and give, all of it or none.

    Attributes:
        game: The game's data-file name.
        persona: The hero the prize goes to.
        day: The rewards' day it is made on.
        product: The prize.
        rare: Whether the prize is rare.
        free: How many free grabs the account has a day, for a free grab; 0 for a paid one.
        wallet: The account's wallet that a paid grab is paid from.
        price: What a paid grab costs.
        name: The id that purchases record a paid grab by.
    """

    game: str
    persona: Persona
    day: date
    product: Product
    rare: bool
    free: int
    wallet: Wallet | None = None
    price: int = 0
    name: str = CLAW


@dataclass(frozen=True, slots=True)
class Grabbed:
    """What a grab gave a hero.

    Attributes:
        hero: The hero.
        product: The prize.
        rare: Whether it is a rare prize.
        before: How the hero held the item before; none when it held none.
        after: How it holds the item now.
        paid: What the grab cost; 0 for a free one.
    """

    hero: Hero
    product: Product
    rare: bool
    before: Held | None
    after: Held
    paid: int


@dataclass(frozen=True, slots=True)
class ClawDay:
    """An account's day at the Claw.

    Attributes:
        rules: The Claw's rules as the instance sets them.
        day: The rewards' day it is.
        used: The day's free grabs, the latest first.
        next_free: When the next day, with its free grabs, starts.
    """

    rules: ClawRules
    day: date
    used: list[Grab]
    next_free: datetime

    @property
    def waiting(self) -> int:
        """How many of the day's free grabs the account has left."""
        return max(0, self.rules.free - len(self.used))


@dataclass(frozen=True, slots=True)
class Prize:
    """One of the prizes a hero may win, with its chance.

    Attributes:
        product: The prize.
        chance: Its chance at each grab.
        tier: The number of its tier, from 0.
        rare: Whether it is rare.
    """

    product: Product
    chance: Fraction
    tier: int
    rare: bool


class ClawStore(Protocol):
    """Where grabs are recorded and their prizes given."""

    async def free_grabs(self, game: str, account_id: int, day: date) -> list[Grab]:
        """Return the free grabs an account made on a day, the latest first.

        Args:
            game: The game's data-file name.
            account_id: The account.
            day: The rewards' day.
        """

    async def grab(self, grab: NewGrab) -> tuple[Held | None, Held]:
        """Record a grab and give its prize, paying a paid one, all of it or none.

        Args:
            grab: The grab.

        Returns:
            How the hero held the item before, none when it held none, and how it holds it now.

        Raises:
            ClawRefused: When the day's free grabs are used, also by another grab at the same
                time, the wallet holds less than the price, or the hero owns the item for good.
        """

    async def feed(self, game: str, limit: int) -> list[Grab]:
        """Return the latest rare prizes of the heroes that others see, the latest first.

        Args:
            game: The game's data-file name.
            limit: The most to return.
        """

    async def counts(
        self, game: str, day: date, since: datetime, currency: str
    ) -> tuple[int, int, int]:
        """Return how many free and paid grabs were made on a day, and what the paid ones cost.

        Args:
            game: The game's data-file name.
            day: The rewards' day.
            since: When the day started.
            currency: The currency whose spending is counted.
        """


class Claw:
    """The Daily Claw of the games' sites: a free grab a day, paid grabs, and their prizes."""

    def __init__(
        self,
        store: ClawStore,
        settings: SettingStore,
        products: ProductStore,
        drops: Drops,
        heroes: Heroes,
        catalogues: Catalogues,
        rewards: Rewards,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Run the Claw from what stores keep.

        Args:
            store: Where grabs are recorded and prizes given.
            settings: The instance's settings, which override the Claw's game data.
            products: Where the store's boxes are kept, whose Claw box holds the prizes.
            drops: Picks a prize for a hero and tells the chances.
            heroes: The accounts' heroes.
            catalogues: The games' imported catalogues, which the prizes are items of.
            rewards: The rewards, whose day the free grabs follow.
            clock: Tells the time; the system's own when none.
        """
        self._store = store
        self._settings = settings
        self._products = products
        self._drops = drops
        self._heroes = heroes
        self._catalogues = catalogues
        self._rewards = rewards
        self._clock = clock or (lambda: datetime.now(UTC))

    async def rules(self, game: Game) -> ClawRules | None:
        """Return a game's Claw with the instance's settings over its data, if it has one.

        Args:
            game: The game.
        """
        return await with_settings(game.claw, game.id, "claw", self._settings)

    async def running(self, game: Game) -> ClawRules | None:
        """Return a game's Claw while it runs: switched on, with the catalogue its prizes need.

        Args:
            game: The game.
        """
        rules = await self.rules(game)
        imported = self._catalogues.contents(game).revision is not None
        return rules if rules and rules.enabled and imported else None

    async def box(self, game: Game) -> Box | None:
        """Return the store's box that holds a game's Claw prizes, once the store has it.

        Args:
            game: The game.
        """
        return await self._products.box_by_key(game.id, CLAW)

    async def day(self, game: Game, rules: ClawRules, account_id: int) -> ClawDay:
        """Return an account's day at a game's Claw.

        Args:
            game: The game.
            rules: The Claw's rules as the instance sets them.
            account_id: The account.
        """
        zone = await self.zone(game)
        day = today(self._clock(), zone)
        used = await self._store.free_grabs(game.id, account_id, day)
        return ClawDay(rules, day, used, day_start(day + timedelta(days=1), zone))

    async def prizes(self, game: Game, hero: Hero) -> tuple[list[Prize], list[Product]]:
        """Return the prizes a hero may win with their chances, in the box's order.

        Args:
            game: The game.
            hero: The hero.

        Returns:
            The prizes, and those that fit the hero but are left out as it owns them for good.
        """
        box = await self.box(game)
        if box is None:
            return [], []
        chances, owned = await self._drops.chances(game, box, hero.persona, hero)
        return _prizes(box, chances), owned

    async def preview(self, game: Game, faction: str, hero_class: str) -> list[Prize]:
        """Return the prizes a hero of a faction and class that owns none of them may win.

        Args:
            game: The game.
            faction: The hero's faction.
            hero_class: The hero's class.
        """
        box = await self.box(game)
        if box is None:
            return []
        return _prizes(box, self._drops.odds(game, box, faction, hero_class))

    async def grab(self, game: Game, account_id: int, hero_id: int, paid: bool) -> Grabbed:
        """Grab a prize for one of an account's heroes, drawn as the grab starts.

        The prize is drawn and given, and a paid grab paid, at once; nothing else changes it.

        Args:
            game: The game.
            account_id: The account.
            hero_id: The persona of the hero the prize goes to.
            paid: Whether the grab is bought rather than one of the day's free ones.

        Raises:
            ClawRefused: When the Claw is off, the free grabs are used or paid grabs off, the
                hero is not one of the account's, the account is short of the price, or the hero
                may get no prize or came to own it meanwhile.
        """
        rules = await self.running(game)
        box = await self.box(game) if rules else None
        if rules is None or box is None:
            raise ClawRefused(ClawRefusal.OFF)
        if paid and not rules.paid.enabled:
            raise ClawRefused(ClawRefusal.PAID_OFF)
        roster = await self._heroes.roster(game, account_id)
        hero = next((h for h in roster.heroes if h.persona.id == hero_id and h.playable), None)
        if hero is None:
            raise ClawRefused(ClawRefusal.UNFIT)
        won = await self._drops.roll(game, box, hero.persona, hero)
        if won is None:
            raise ClawRefused(ClawRefusal.NOTHING)
        rare = any(drop.rare for drop in box.drops if drop.product == won)
        day = today(self._clock(), await self.zone(game))
        wallet, price = None, 0
        if paid:
            priced = await account_rules(game, self._settings)
            wallet = account_wallet(priced, account_id, rules.paid.currency)
            price = rules.paid.price
        grab = NewGrab(
            game.id,
            hero.persona,
            day,
            won,
            rare,
            0 if paid else rules.free,
            wallet,
            price,
            rules.name,
        )
        before, after = await self._store.grab(grab)
        return Grabbed(hero, won, rare, before, after, price)

    async def today(self, game: Game, rules: ClawRules) -> tuple[int, int, int]:
        """Return how many free and paid grabs the rewards' day so far has, and what they cost.

        Args:
            game: The game.
            rules: The Claw's rules as the instance sets them, whose paid grabs' currency counts.
        """
        zone = await self.zone(game)
        day = today(self._clock(), zone)
        since = day_start(day, zone)
        return await self._store.counts(game.id, day, since, rules.paid.currency)

    async def feed(self, game: Game, rules: ClawRules) -> list[Grab]:
        """Return the latest rare prizes of the heroes that others see, while the feed is on.

        Args:
            game: The game.
            rules: The Claw's rules as the instance sets them.
        """
        return await self._store.feed(game.id, rules.feed.length) if rules.feed.enabled else []

    async def zone(self, game: Game) -> str:
        """Return the time zone at whose midnight the free grabs' days start: the rewards'.

        Args:
            game: The game.
        """
        rules = await self._rewards.rules(game)
        return rules.zone if rules else "UTC"


def _prizes(box: Box, chances: Mapping[Product, Fraction]) -> list[Prize]:
    return [
        Prize(drop.product, chances[drop.product], drop.tier, drop.rare)
        for drop in box.drops
        if drop.product in chances
    ]
