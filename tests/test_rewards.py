import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

import anyio
import pytest
from accounts import execute, select_value
from heroes import create_hero
from living import NATIONAL, ROYAL, import_catalogue, setting, worn
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER

from openfesl.core.catalogues import Catalogues
from openfesl.core.chests import key_tag
from openfesl.core.drops import Drops
from openfesl.core.games import Game, load_games
from openfesl.core.heroes import Heroes
from openfesl.core.inventory import COUNTED, Ownership
from openfesl.core.reward_claims import Grant
from openfesl.core.rewards import Reward, RewardRefusal, RewardRefused, Rewards, RewardState
from openfesl.core.store import Product
from openfesl.core.wallets import Wallet
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.entitlements import SqlEntitlementStore, add_account_item
from openfesl.storage.heroes import SqlHeroStore
from openfesl.storage.personas import SqlPersonaStore
from openfesl.storage.purchases import SqlPurchaseStore
from openfesl.storage.rewards import SqlRewardStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.stats import SqlStatStore
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio

# An invented catalogue: the masks that Santa's and Nikolaus' Visits may give, the Royal
# birthday pack, the tonics that the Visits and the rewards give instead of an item a hero owns
# for good, and the Royal soldier's starter pack.
INVENTED = {
    1081: worn("Invented Mask", ROYAL, "1"),
    1084: worn("Invented Visor", NATIONAL, "1"),
    1439: worn("Invented Party Hat", ROYAL, "0"),
    1000: worn("Invented Legs", ROYAL, "6"),
    998: worn("Invented Body", ROYAL, "3"),
    5063: ("Invented Tricycle", "emote", {"faction": ROYAL}),
    2161: ("Invented Flare", "ability", {"faction": ROYAL, "consumable": "true"}),
    2153: ("Invented Tonic", "ability", {"consumable": "true"}),
    3015: ("Invented Rifle", "weapon", {"faction": ROYAL, "kits": "2"}),
    9000: ("Invented XP Boost", "convenience", {}),
    9040: ("Invented Stamina Boost", "convenience", {}),
}
# The fifth day of Christmas, and the birthday a year apart.
CHRISTMAS = datetime(2026, 12, 5, 12, tzinfo=UTC)
BIRTHDAYS = (datetime(2026, 6, 26, tzinfo=UTC), datetime(2027, 6, 30, tzinfo=UTC))


class Site:
    """The rewards of the game's site at a time that the test sets, with the heroes' items."""

    def __init__(self, game: Game, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.game = game
        self.now = CHRISTMAS
        self.catalogues = Catalogues(SqlCatalogueStore(sessions), None, load_games())
        self.entitlements = SqlEntitlementStore(sessions)
        self.personas = SqlPersonaStore(sessions)
        stats = SqlStatStore(sessions)
        heroes = Heroes(SqlHeroStore(sessions), stats)
        # The same drops on every run.
        drops = Drops(self.catalogues, self.entitlements, Random(20111201))
        self.rewards = Rewards(
            SqlRewardStore(sessions),
            SqlSettingStore(sessions),
            heroes,
            stats,
            self.entitlements,
            self.catalogues,
            SqlProductStore(sessions),
            drops,
            lambda: self.now,
        )

    async def account(self, hero: int) -> int:
        persona = await self.personas.find_by_id(hero)
        assert persona is not None
        return persona.account_id

    async def available(self, hero: int) -> list[Reward]:
        return await self.rewards.available(self.game, await self.account(hero))

    async def claim(self, hero: int, key: str, faction: str | None) -> list[int] | RewardRefusal:
        """Claim a reward of a hero's account for the hero, or for the account without one."""
        account = await self.account(hero)
        recipient = hero if faction else None
        try:
            return await self.rewards.claim(self.game, account, key, faction, recipient)
        except RewardRefused as refused:
            return refused.refusal

    async def held(self, hero: int) -> dict[str, int | None]:
        """Return a hero's items by tag, with their uses."""
        return {entry.tag: entry.uses for entry in await self.entitlements.of_persona(hero)}


@pytest.fixture
async def site(
    prompt_actions: None,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> AsyncIterator[Site]:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED)
    engine = create_async_engine(database_url)
    found = Site(bfheroes, async_sessionmaker(engine))
    await found.catalogues.load()
    yield found
    await engine.dispose()


def test_game_data_that_rewards_a_withheld_item_does_not_load(bfheroes: Game) -> None:
    data = bfheroes.model_dump()
    # An item of the Royal Valentine's gift, as if the game data withheld it.
    data["catalogue"]["withheld"] = [1127]

    with pytest.raises(ValidationError, match=r"rewards give withheld items: \[1127\]"):
        Game.model_validate(data)


async def test_a_claim_and_a_key_purchase_for_one_account_at_once_both_go_through(
    base: str, database_url: str, monkeypatch: pytest.MonkeyPatch, bfheroes: Game
) -> None:
    game = bfheroes
    assert game.chests is not None
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine)
    persona = await SqlPersonaStore(sessions).find_by_id(hero)
    assert persona is not None
    account = persona.account_id
    wallet = Wallet("bfheroes", "_PF", account, None, 2500)
    keys = key_tag(game.chests)
    claim = Grant(
        game="bfheroes",
        account_id=account,
        key="first-hero",
        faction=None,
        hero=None,
        items=(),
        fallback=(2153, Ownership(COUNTED, uses=150)),
        funds=(wallet, 100),
        keys=(keys, 1),
    )
    key = Product(1, game.chests.key, Ownership(COUNTED, uses=1), "_PF", 300, None, 0)
    # The claim adds its keys only once the purchase has added its own, so that a claim holding
    # the wallet by then would deadlock on every run.
    claiming, buying = asyncio.Event(), asyncio.Event()

    async def claimed_keys(*args: Any) -> int:
        claiming.set()
        await buying.wait()
        return await add_account_item(*args)

    async def bought_keys(*args: Any) -> int:
        await claiming.wait()
        held = await add_account_item(*args)
        buying.set()
        return held

    monkeypatch.setattr("openfesl.storage.rewards.add_account_item", claimed_keys)
    monkeypatch.setattr("openfesl.storage.purchases.add_account_item", bought_keys)
    with anyio.fail_after(10):
        await asyncio.gather(
            SqlRewardStore(sessions).grant(claim),
            SqlPurchaseStore(sessions).buy_for_account(account, "key", key, wallet),
        )
    await engine.dispose()

    funds = "SELECT balance FROM wallet WHERE currency = '_PF' AND account_id = :account"
    assert await select_value(database_url, funds, account=account) == 2500 + 100 - 300
    held = "SELECT use_count FROM entitlement WHERE account_id = :account AND tag = :tag"
    assert await select_value(database_url, held, account=account, tag=keys) == 2


async def test_each_day_of_christmas_gives_each_faction_one_roll_of_its_visit_for_one_hero(
    site: Site, base: str
) -> None:
    royal = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    national = await create_hero(base, "ann@example.test", "Mudlark")
    other = await create_hero(base, "bob@example.test", "Tinpot")

    today = [reward for reward in await site.available(royal) if reward.day]
    unfit = await site.claim(national, "christmas:2026:5", "royal")
    given = await site.claim(royal, "christmas:2026:5", "royal")
    again = await site.claim(royal, "christmas:2026:5", "royal")
    racing = await asyncio.gather(
        *(site.claim(national, "christmas:2026:5", "national") for _ in range(2))
    )
    site.now = datetime(2026, 12, 6, tzinfo=UTC)
    missed = await site.claim(other, "christmas:2026:5", "national")
    tomorrow = [reward for reward in await site.available(royal) if reward.day]
    next_day = await site.claim(other, "christmas:2026:6", "national")

    # Santa's Gift for Royal heroes and Nikolaus' Gift for National ones, until midnight.
    assert [(r.faction, r.name, r.state, r.heroes) for r in today] == [
        ("royal", "REWARDS_SANTAS_GIFT_DAY_5_NAME", RewardState.CLAIMABLE, (royal,)),
        ("national", "REWARDS_NIKOLAUS_GIFT_DAY_5_NAME", RewardState.CLAIMABLE, (national,)),
    ]
    assert {r.ends for r in today} == {datetime(2026, 12, 6, tzinfo=UTC)}
    assert unfit is RewardRefusal.UNFIT
    # One of the Visit's drops that the invented catalogue holds: the mask, flares or tonics.
    assert isinstance(given, list) and len(given) == 1 and set(given) <= {1081, 2161, 2153}
    assert again is RewardRefusal.CLAIMED
    # Two claims at once give once.
    assert RewardRefusal.CLAIMED in racing
    assert sum(isinstance(result, list) for result in racing) == 1
    # A day missed is gone, while the next day's gifts wait for each account.
    assert missed is RewardRefusal.UNKNOWN
    assert [(r.key, r.state) for r in tomorrow] == [
        ("christmas:2026:6", RewardState.CLAIMABLE),
        ("christmas:2026:6", RewardState.CLAIMABLE),
    ]
    assert isinstance(next_day, list)


async def test_a_yearly_pack_goes_once_to_each_faction_and_an_item_owned_for_good_turns_to_tonics(
    site: Site, base: str, database_url: str
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    claimed = []
    for moment in BIRTHDAYS:
        site.now = moment
        key = f"birthday:{moment.year}"
        claimed.append([await site.claim(hero, key, faction) for faction in ("royal", None)])
    held = await site.held(hero)
    await setting(database_url, "bfheroes.rewards.paused", "on")
    paused = await site.claim(hero, "birthday:2027", "national")

    # The hat for good, the flares, a week of legs and body and three days of the tricycle; a
    # year later the hat the hero owns turns into tonics, the others add uses and time.
    assert claimed == [
        [[998, 1000, 1439, 2161, 5063], []],
        [[998, 1000, 2153, 2161, 5063], []],
    ]
    assert (held["1439-unlm"], held["2161-lmct"], held["2153-lmct"]) == (None, 50, 150)
    # The Funds of both years for the account, and its two keys of each year.
    funds = "SELECT sum(delta) FROM wallet_entry WHERE reason = 'reward'"
    keys = "SELECT use_count FROM entitlement WHERE tag = '7353-lmct' AND persona_id IS NULL"
    assert (await select_value(database_url, funds), await select_value(database_url, keys)) == (
        924,
        4,
    )
    assert paused is RewardRefusal.PAUSED


async def test_a_new_hero_and_goals_bring_their_gifts_once_and_a_first_chest_brings_a_key(
    site: Site, base: str, database_url: str
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    site.now = datetime.now(UTC) + timedelta(days=1)
    level, matches = f"milestone:level-10:{hero}", "milestone:matches"

    before = await site.available(hero)
    early = [await site.claim(hero, key, None) for key in (level, "first-chest")]
    starter = await site.claim(hero, f"starter:{hero}", "royal")
    first = [await site.claim(hero, "milestone:first-hero", None) for _ in range(2)]
    # The hero reaches level 10 and finishes 100 ranked matches; a server stacks a chest on it.
    await execute(
        database_url,
        "UPDATE stat SET value = 10 WHERE owner_id = :hero AND key = 'level'",
        hero=hero,
    )
    for key, value in (("win", 60), ("los", 40)):
        await execute(
            database_url,
            "INSERT INTO stat SELECT partition, owner_type, owner_id, period, period_start,"
            " :key, :value, '' FROM stat WHERE owner_id = :hero AND key = 'level'",
            hero=hero,
            key=key,
            value=value,
        )
    await execute(
        database_url,
        "INSERT INTO entitlement (account_id, persona_id, tag, use_count, status) SELECT"
        " account_id, id, 'BFH_TREASURE_CHEST', 1, 'ACTIVE' FROM persona WHERE id = :hero",
        hero=hero,
    )
    later = [
        await site.claim(hero, key, None)
        for key in (level, f"milestone:level-20:{hero}", matches, "first-chest")
    ]
    site.now += timedelta(days=30)
    a_month_on = await site.available(hero)

    assert always(before) == {
        f"starter:{hero}": (RewardState.CLAIMABLE, None),
        "milestone:first-hero": (RewardState.CLAIMABLE, 1),
        level: (RewardState.LOCKED, 1),
        f"milestone:level-20:{hero}": (RewardState.LOCKED, 1),
        f"milestone:level-30:{hero}": (RewardState.LOCKED, 1),
        matches: (RewardState.LOCKED, 0),
        "first-chest": (RewardState.LOCKED, None),
    }
    assert early == [RewardRefusal.LOCKED, RewardRefusal.LOCKED]
    # The soldier's rifle and both boosts for a week, and tonics.
    assert starter == [2153, 3015, 9000, 9040]
    assert first == [[], RewardRefusal.CLAIMED]
    assert later == [[], RewardRefusal.LOCKED, [], []]
    # 600 for the first hero, 500 for its level 10 and 1,000 for the matches; one key.
    funds = "SELECT sum(delta) FROM wallet_entry WHERE reason = 'reward'"
    keys = "SELECT use_count FROM entitlement WHERE tag = '7353-lmct' AND persona_id IS NULL"
    assert (await select_value(database_url, funds), await select_value(database_url, keys)) == (
        2100,
        1,
    )
    # The starter pack is gone a month after the hero's creation.
    assert f"starter:{hero}" not in always(a_month_on)
    assert always(a_month_on)[level] == (RewardState.CLAIMED, 10)


def always(rewards: list[Reward]) -> dict[str, tuple[RewardState, int | None]]:
    """Return the rewards that no event brings, by key, with their state and progress."""
    return {
        r.key: (r.state, r.progress)
        for r in rewards
        if r.key.split(":")[0] in ("starter", "milestone", "first-chest")
    }
