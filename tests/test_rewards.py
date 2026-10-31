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
from living import NATIONAL, ROYAL, freeze, import_catalogue, setting, worn
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
# for good, the Royal soldier's starter pack and the Royal Halloween missions' rewards.
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
    3206: ("Invented Golden Machete", "weapon", {"faction": ROYAL, "kits": "1"}),
    1512: worn("Invented Halloween Shirt", ROYAL, "3"),
}
# The fifth day of Christmas, and the birthday a year apart.
CHRISTMAS = datetime(2026, 12, 5, 12, tzinfo=UTC)
# A day without events, which the server's own pages see while a test makes its heroes, so that
# they take no event's baselines before the test's clock does.
QUIET = datetime(2026, 3, 2, tzinfo=UTC)
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
        settings = SqlSettingStore(sessions)
        heroes = Heroes(SqlHeroStore(sessions), stats, settings)
        # The same drops on every run.
        drops = Drops(self.catalogues, self.entitlements, Random(20111201))
        self.rewards = Rewards(
            SqlRewardStore(sessions),
            settings,
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
            given = await self.rewards.claim(self.game, account, key, faction, recipient)
        except RewardRefused as refused:
            return refused.refusal
        return [item_id for item_id, _ in given.items]

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
    monkeypatch: pytest.MonkeyPatch,
    bfheroes: Game,
) -> AsyncIterator[Site]:
    freeze(monkeypatch, QUIET)
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
        at=CHRISTMAS,
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
            SqlPurchaseStore(sessions).buy_for_account(account, "key", key, wallet, 1),
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
    history = await site.rewards.history(site.game, await site.account(royal))
    running = await site.rewards.running(site.game)
    upcoming = await site.rewards.upcoming(site.game)
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
    # The account's claims of the year name what they were and whose they are, the latest first.
    assert [(c.name, c.claim.hero, c.claim.at) for c in history] == [
        ("REWARDS_NIKOLAUS_GIFT_DAY_5_NAME", national, CHRISTMAS),
        ("REWARDS_SANTAS_GIFT_DAY_5_NAME", royal, CHRISTMAS),
    ]
    assert [(run.key, run.day, run.days) for run in running] == [("christmas", 5, 24)]
    # The next event is the next year's Valentine's Day.
    assert (upcoming[0].key, upcoming[0].period.start) == (
        "valentine",
        datetime(2027, 2, 13, tzinfo=UTC),
    )
    # A day missed is gone, while the next day's gifts wait for each account.
    assert missed is RewardRefusal.UNKNOWN
    assert [(r.key, r.state) for r in tomorrow] == [
        ("christmas:2026:6", RewardState.CLAIMABLE),
        ("christmas:2026:6", RewardState.CLAIMABLE),
    ]
    assert isinstance(next_day, list)


async def test_an_event_run_past_the_year_end_keeps_its_days_and_claims_under_its_own_year(
    site: Site, base: str, database_url: str
) -> None:
    royal = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    # As the admin panel saves Christmas run to 9 January, with the admin's own text.
    await setting(
        database_url, "bfheroes.rewards.events.christmas.window", '{"first": "12-01", "count": 40}'
    )
    await setting(
        database_url, "bfheroes.rewards.events.christmas.own_text", '{"en": "A gift a day."}'
    )
    site.now = datetime(2027, 1, 3, 12, tzinfo=UTC)

    today = [reward for reward in await site.available(royal) if reward.day]
    given = await site.claim(royal, "christmas:2026:34", "royal")
    again = await site.claim(royal, "christmas:2026:34", "royal")
    history = await site.rewards.history(site.game, await site.account(royal))

    # The 34th day of the run that began in 2026, named by the event's title with its own text.
    assert [(r.key, r.name, r.day, r.state) for r in today] == [
        ("christmas:2026:34", "games.bfheroes.rewards.christmas", 34, RewardState.CLAIMABLE),
        ("christmas:2026:34", "games.bfheroes.rewards.christmas", 34, RewardState.LOCKED),
    ]
    assert isinstance(given, list) and again is RewardRefusal.CLAIMED
    assert [claimed.name for claimed in history] == ["games.bfheroes.rewards.christmas"]


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
    site.now += timedelta(days=2)
    days_later = await site.available(hero)

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
    # The starter pack stays a day after its last one, then goes.
    assert always(a_month_on)[f"starter:{hero}"] == (RewardState.CLAIMED, None)
    assert f"starter:{hero}" not in always(days_later)
    assert always(a_month_on)[level] == (RewardState.CLAIMED, 10)


async def kills(database_url: str, hero: int, stat: str, value: int) -> None:
    """Set a hero's stat as its dedicated server reports it at a round's end."""
    await execute(
        database_url,
        "INSERT INTO stat SELECT partition, owner_type, owner_id, period, period_start, :key,"
        " :value, '' FROM stat WHERE owner_id = :hero AND key = 'level'"
        " ON CONFLICT (partition, owner_type, owner_id, period, period_start, key)"
        " DO UPDATE SET value = :value",
        hero=hero,
        key=stat,
        value=value,
    )


async def test_a_mission_counts_from_its_events_start_and_closes_at_its_deadline(
    site: Site, base: str, database_url: str
) -> None:
    commando = await create_hero(
        base, "ann@example.test", "Cutthroat", faction="royal", **{"class": "commando"}
    )
    soldier = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    machete, shirt = "halloween-machete:2026", "halloween-map:2026"
    # Machete kills from before Halloween, which do not count.
    await kills(database_url, commando, "kw106", 50)

    def missions(rewards: list[Reward]) -> dict[str, tuple[RewardState, int | None, int | None]]:
        return {
            r.key: (r.state, r.hero, r.progress) for r in rewards if r.faction == "royal" and r.goal
        }

    site.now = datetime(2026, 10, 19, tzinfo=UTC)
    started = missions(await site.available(commando))
    await kills(database_url, commando, "kw106", 600)
    short = await site.claim(commando, machete, "royal")
    await kills(database_url, commando, "kw106", 750)
    # Wins on Perilous Port by a hero that had none when Halloween began.
    await kills(database_url, soldier, "m_win12", 10)
    reached = missions(await site.available(commando))
    claimed = [await site.claim(hero, machete, "royal") for hero in (soldier, commando)]
    site.now = datetime(2026, 11, 1, tzinfo=UTC)
    late = await site.claim(soldier, shirt, "royal")
    site.now = datetime(2026, 11, 25, tzinfo=UTC)
    rising_off = missions(await site.available(commando))
    await setting(database_url, "bfheroes.rewards.events.rising-sun.enabled", "on")
    rising_on = missions(await site.available(commando))

    assert started == {
        machete: (RewardState.LOCKED, commando, 0),
        shirt: (RewardState.LOCKED, None, 0),
    }
    assert short is RewardRefusal.LOCKED
    assert reached == {
        machete: (RewardState.CLAIMABLE, commando, 700),
        shirt: (RewardState.CLAIMABLE, soldier, 10),
    }
    # The Golden Machete goes to a Commando of the faction alone.
    assert claimed == [RewardRefusal.UNFIT, [3206]]
    assert late is RewardRefusal.UNKNOWN
    # The Rising Sun mission runs only once switched on, and needs a Gunner the account lacks.
    assert rising_off == {}
    assert rising_on == {"rising-sun:2026": (RewardState.LOCKED, None, 0)}


def always(rewards: list[Reward]) -> dict[str, tuple[RewardState, int | None]]:
    """Return the rewards that no event brings, by key, with their state and progress."""
    return {
        r.key: (r.state, r.progress)
        for r in rewards
        if r.key.split(":")[0] in ("starter", "milestone", "first-chest")
    }


async def test_a_daily_gift_of_a_day_with_claims_paused_waits_until_its_event_ends(
    site: Site, base: str, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    site.now = datetime(2026, 12, 3, 12, tzinfo=UTC)
    await setting(database_url, "bfheroes.rewards.paused", "on")
    paused = await site.claim(hero, "christmas:2026:3", "royal")
    # The server finds claims paused on the third day, which no account looks at; they go on
    # again the next.
    monkeypatch.setattr("openfesl.core.rewards.PAUSE_CHECK_INTERVAL", 0.01)
    third = "SELECT count(*) FROM reward_pause WHERE day = '2026-12-03'"
    following = asyncio.create_task(site.rewards.follow_pauses([site.game]))
    with anyio.fail_after(5):
        while not await select_value(database_url, third):
            await asyncio.sleep(0.02)
    following.cancel()
    await setting(database_url, "bfheroes.rewards.paused", "off")
    site.now = datetime(2026, 12, 4, 12, tzinfo=UTC)
    # The Royal Christmas gifts alone: the hero's starter pack is not one of them.
    waiting = {
        (r.key, r.kept, r.ends)
        for r in await site.available(hero)
        if (r.event, r.faction) == ("christmas", "royal")
    }
    late = await site.claim(hero, "christmas:2026:3", "royal")
    after = {(r.key, r.faction) for r in await site.available(hero)}
    site.now = datetime(2026, 12, 25, tzinfo=UTC)
    ended = await site.claim(hero, "christmas:2026:4", "royal")

    assert paused is RewardRefusal.PAUSED
    # The paused day's gift waits until Christmas Eve ends; the day before it is gone.
    assert waiting == {
        ("christmas:2026:4", False, datetime(2026, 12, 5, tzinfo=UTC)),
        ("christmas:2026:3", True, datetime(2026, 12, 25, tzinfo=UTC)),
    }
    assert isinstance(late, list) and len(late) == 1
    assert ("christmas:2026:3", "royal") not in after
    assert ("christmas:2026:4", "royal") in after
    assert ended is RewardRefusal.UNKNOWN
