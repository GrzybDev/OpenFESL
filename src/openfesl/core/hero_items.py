from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from openfesl.core.catalogues import Catalogue, Catalogues, Contents, Item
from openfesl.core.entitlements import ACTIVE, Entitlement, EntitlementStore
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, TransactionError
from openfesl.core.games import Game
from openfesl.core.hero_rules import HeroRules, WrittenStat
from openfesl.core.inventory import PERMANENT, Held
from openfesl.core.personas import Persona
from openfesl.core.stats import (
    ACCOUNT,
    PERSONA,
    Owner,
    StatStore,
    Value,
    bucket_of,
    partition_of,
    stat_owners,
)
from openfesl.protocol.rank import Period, StatUpdate, UpdateStats, UpdateType


def held(
    rules: HeroRules, faction: str | None, hero_class: str | None, kept: Iterable[Entitlement]
) -> set[int]:
    """Return the numbers of the items a hero holds, apart from those its level brings.

    A hero holds the loadout of its faction and class, which the game grants it, and the items
    kept for it while they are in use and have time left.

    Args:
        rules: The game's hero rules.
        faction: The hero's faction, if it has one.
        hero_class: The hero's class, if it has one.
        kept: The entitlements kept for the hero.
    """
    now = datetime.now(UTC)
    tags = (
        item.tag.partition("-")[0]
        for item in kept
        if item.status == ACTIVE and (item.expires is None or item.expires > now)
    )
    # The tags of what is neither worn nor used, such as a treasure chest's, name no item.
    items = {int(number) for number in tags if number.isascii() and number.isdecimal()}
    return items | granted(rules, faction, hero_class)


def holding(item_id: int, kept: Iterable[Entitlement]) -> Held:
    """Return how a hero holds an item it holds: as its entitlement says, else for good.

    The game grants a hero its loadout itself, so an item held without an entitlement is held
    for good.

    Args:
        item_id: The item's number.
        kept: The entitlements kept for the hero that are in use and have time left.
    """
    prefix = f"{item_id}-"
    for entry in kept:
        if entry.tag.startswith(prefix):
            return Held(entry.tag.removeprefix(prefix), entry.expires, entry.uses)
    return Held(PERMANENT)


def granted(rules: HeroRules | None, faction: str | None, hero_class: str | None) -> set[int]:
    """Return the numbers of the items the game grants a hero itself: its loadout's.

    Args:
        rules: The game's hero rules; none for a game whose heroes are not made on its site.
        faction: The hero's faction, if it has one.
        hero_class: The hero's class, if it has one.
    """
    if rules is None or not (faction and hero_class):
        return set()
    loadout = rules.loadouts[faction][hero_class]
    return {*loadout.appearance, *loadout.equipment, *loadout.emotes}


def fits(
    item: Item, kinds: Collection[str], faction: str | None, hero_class: str | None, level: int
) -> bool:
    """Tell whether a hero may use an item in a stat, as the game's definitions say.

    Args:
        item: The item, from the game's catalogue.
        kinds: The kinds of the items the stat holds.
        faction: The hero's faction, if it has one.
        hero_class: The hero's class, if it has one.
        level: The hero's level.
    """
    return (
        item.kind in kinds
        and faction in item.factions
        and hero_class in item.classes
        and (item.level or 0) <= level
    )


@dataclass(frozen=True, slots=True)
class _Hero:
    faction: str | None
    hero_class: str | None
    level: int
    held: set[int]
    # What the checks read of the hero's stats and its account's, by owner and key.
    stats: Mapping[tuple[Owner, str], Value]
    owner: Owner
    account: Owner


class OwnWrites:
    """The stats of its hero and of its account that a player's own game may write.

    The game keeps its hero's look, equipment, emotes and wishes itself, and a few facts of its
    account. Nothing else of a player's comes from the player's game: progress comes from the
    rounds that dedicated servers report.
    """

    def __init__(
        self, stats: StatStore, entitlements: EntitlementStore, catalogues: Catalogues
    ) -> None:
        """Check the writes against the heroes' stats, the items kept for them and the catalogue.

        Args:
            stats: Where the heroes' faction, class and level are kept.
            entitlements: Where the items kept for each hero are.
            catalogues: The games' imported catalogues, which tell whether an item fits.
        """
        self._stats = stats
        self._entitlements = entitlements
        self._catalogues = catalogues

    async def owners(
        self, connection: FeslConnection, game: Game, request: UpdateStats
    ) -> list[dict[str, Owner]]:
        """Return whom each stat of a player's write is kept under, once all of it may be written.

        The game may set for all time the stats that the game's data lets it write, of the hero
        it plays as and of its account, to values that the hero may hold: numbers within their
        range and items the hero holds, its own hair and the base items of its faction among
        them. With the game's imported catalogue the items must also fit the stat, the hero's
        faction, class and level, and be worn one per slot, while the hero also holds the items
        that its level brings, and a mission it newly wishes for must be one the game lets it
        take; without it, the server cannot tell how an item fits nor which items the game
        grants by level.

        Args:
            connection: The FESL connection the request came on.
            game: The session's game.
            request: Each owner's updates.

        Raises:
            TransactionError: When anything in the request may not be written.
        """
        session = connection.session
        persona, account, rules = session.persona, session.account, game.heroes
        if persona is None or account is None or rules is None:
            raise TransactionError(PARAMETERS_ERROR)
        mine = {Owner(PERSONA, persona.id), Owner(ACCOUNT, account.id)}
        owned = []
        written: list[tuple[str, WrittenStat, list[int]]] = []
        for updates in request.owners:
            keys = [update.key for update in updates.stats]
            owners = stat_owners(connection, game, updates.owner, updates.owner_type, keys)
            if not set(owners.values()) <= mine:
                raise TransactionError(PARAMETERS_ERROR)
            owned.append(owners)
            written.extend(_written(rules, update) for update in updates.stats)
        named = [entry for entry in written if entry[1].items]
        if named and not await self._may_hold(game, rules, persona, account.id, named):
            raise TransactionError(PARAMETERS_ERROR)
        return owned

    async def _may_hold(
        self,
        game: Game,
        rules: HeroRules,
        persona: Persona,
        account_id: int,
        named: Sequence[tuple[str, WrittenStat, list[int]]],
    ) -> bool:
        contents = self._catalogues.contents(game)
        found = {
            number: contents.items.get(number)
            for _, _, numbers in named
            for number in numbers
            if number
        }
        hero = await self._hero(game, rules, persona, account_id, contents, named, found.values())
        if contents.revision is None:
            return found.keys() <= hero.held
        for key, stat, numbers in named:
            items = [found[number] for number in numbers if number]
            # What the stat holds already was taken before, so the game may send it again.
            stored = hero.stats.get((hero.owner, key))
            before = set(stat.numbers(stored.value, stored.text) or []) if stored else set()
            if not all(
                item
                and fits(item, stat.items, hero.faction, hero.hero_class, hero.level)
                and (item.id in hero.held or item.kind in rules.granted)
                and (item.id in before or _may_take(item, rules, contents, hero))
                for item in items
            ):
                return False
            slots = [item.slot for item in items if item and item.slot]
            if len(slots) != len(set(slots)):
                return False
        return True

    async def _hero(
        self,
        game: Game,
        rules: HeroRules,
        persona: Persona,
        account_id: int,
        contents: Contents,
        named: Sequence[tuple[str, WrittenStat, list[int]]],
        items: Iterable[Item | None],
    ) -> _Hero:
        owner, account = Owner(PERSONA, persona.id), Owner(ACCOUNT, account_id)
        keys = rules.stats
        missions = {
            mission
            for item in items
            if item
            for mission in (item.prerequisite, None if item.repeatable else item.id)
            if mission
        }
        found = await self._stats.values(
            partition_of(game),
            [owner, account],
            [
                keys.faction,
                keys.hero_class,
                keys.level,
                keys.hair_style,
                keys.facial_hair,
                keys.daily_completed,
                *(key for key, _, _ in named),
                *(_completed(rules, mission) for mission in missions),
            ],
            bucket_of(Period.ALL_TIME, 0),
        )

        def number(key: str) -> int | None:
            return _number(found, owner, key)

        faction = rules.faction_of(number(keys.faction))
        hero_class = rules.class_of(number(keys.hero_class))
        owned = held(rules, faction, hero_class, await self._entitlements.of_persona(persona.id))
        owned |= {number(keys.hair_style) or 0, number(keys.facial_hair) or 0} - {0}
        if faction and hero_class:
            owned |= {*rules.loadouts[faction][hero_class].missions}
            if contents.revision is None:
                owned |= {*rules.factions[faction].base_items}
            else:
                owned |= {
                    item.id
                    for item in contents.items.values()
                    if item.base and faction in item.factions and hero_class in item.classes
                }
        level = max(number(keys.level) or 1, 1)
        return _Hero(faction, hero_class, level, owned, found, owner, account)


def _written(rules: HeroRules, update: StatUpdate) -> tuple[str, WrittenStat, list[int]]:
    # The game sets its own stats for all time; adding to them would leave their range.
    stat = rules.written_stat(update.key)
    numbers = stat.numbers(update.value, update.text) if stat else None
    if (
        stat is None
        or numbers is None
        or update.kind is not UpdateType.SET
        or update.periods != (Period.ALL_TIME,)
    ):
        raise TransactionError(PARAMETERS_ERROR)
    return update.key, stat, numbers


def _may_take(item: Item, rules: HeroRules, contents: Contents, hero: _Hero) -> bool:
    # The game's rules for taking a mission: the mission to complete first, one completed before
    # only when it repeats, and a daily mission only on its own day while the account has not
    # completed that day's (BFHeroes.exe 1.89 0x007c1609).
    def completed(mission: int) -> bool:
        return (_number(hero.stats, hero.owner, _completed(rules, mission)) or 0) > 0

    if item.prerequisite and not completed(item.prerequisite):
        return False
    if not item.repeatable and completed(item.id):
        return False
    if item.id in contents.daily_missions:
        today = _today()
        # The game reads the day of a completion never stored as 0, 1 January.
        done = _number(hero.stats, hero.account, rules.stats.daily_completed) or 0
        todays = Catalogue(contents).daily_mission(today)
        return item.id == todays and done != today.timetuple().tm_yday - 1
    return True


def _number(found: Mapping[tuple[Owner, str], Value], owner: Owner, key: str) -> int | None:
    value = found.get((owner, key))
    return int(value.value) if value else None


def _completed(rules: HeroRules, mission: int) -> str:
    return rules.stats.completed.replace("{id}", str(mission))


def _today() -> date:
    return datetime.now(UTC).date()
