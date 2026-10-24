from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game
from openfesl.core.hosting import Hosting
from openfesl.core.personas import NOT_LOGGED_IN, UNKNOWN_PERSONA, PersonaStore
from openfesl.protocol.message import Message
from openfesl.protocol.rank import (
    GetStats,
    GetStatsAnswer,
    GetStatsForOwners,
    GetStatsForOwnersAnswer,
    OwnerStats,
    OwnerUpdates,
    Period,
    Stat,
    UpdateStats,
    UpdateType,
)

PERSONA = 1
# The SDK's owner type for values of the whole game, which dedicated servers only add to.
GAME_WIDE = 11

# Bounds one request's answer or writes; a dedicated server asks for its game's whole template.
MAXIMUM_ENTRIES = 16384

_MONDAY = date(1970, 1, 5)
_ALL_TIME = date(1970, 1, 1)
_DAYS = {Period.DAILY: 1, Period.WEEKLY: 7, Period.BIWEEKLY: 14}
_MONTHS = {Period.MONTHLY: 1, Period.QUARTERLY: 3, Period.YEARLY: 12}


@dataclass(frozen=True, slots=True)
class Owner:
    """Whose stats: a persona, the whole game or another owner type the SDK knows.

    Attributes:
        type: The owner type, 1 for a persona.
        id: The persona's number, or the id the game uses for the owner.
    """

    type: int
    id: int


@dataclass(frozen=True, slots=True)
class Bucket:
    """One period's values, such as the week that started on a Monday.

    Attributes:
        period: The kind of period.
        start: Its first day in UTC; every all-time value shares one start.
    """

    period: Period
    start: date


@dataclass(frozen=True, slots=True)
class Value:
    """A stored stat: a number and an optional text, such as the date of an update."""

    value: float
    text: str


@dataclass(frozen=True, slots=True)
class Change:
    """One update of one owner's stat in one period.

    Attributes:
        owner: Whose stat.
        key: The stat's key.
        bucket: The period it updates.
        kind: How the value combines with the stored one.
        value: The number to combine.
        text: The text to keep; an empty one keeps the stored text.
    """

    owner: Owner
    key: str
    bucket: Bucket
    kind: UpdateType
    value: float
    text: str


class StatStore(Protocol):
    """Where the stats of a game's partition are kept."""

    async def values(
        self, partition: str, owners: Sequence[Owner], keys: Sequence[str], bucket: Bucket
    ) -> dict[tuple[Owner, str], Value]:
        """Return the stored stats of owners, leaving out those never stored.

        Args:
            partition: The game's partition.
            owners: Whose stats.
            keys: The stats' keys.
            bucket: The period.
        """

    async def apply(self, partition: str, changes: Sequence[Change]) -> None:
        """Apply updates in their order, all of them or none.

        A value never stored counts as 0, which is the game's own default: games send their
        values as offsets from the defaults in their own data.

        Args:
            partition: The game's partition.
            changes: The updates.
        """


def period_start(period: Period, day: date, past: int = 0) -> date:
    """Return the first day of a period, counted in UTC.

    Weeks start on Mondays and two-week periods on every other Monday since 1970-01-05.

    Args:
        period: The kind of period.
        day: A day within the current period.
        past: How many periods back; -1 gives the start of the next one.
    """
    if days := _DAYS.get(period):
        return _MONDAY + timedelta(days=((day - _MONDAY).days // days - past) * days)
    if months := _MONTHS.get(period):
        index = day.year * 12 + (day.month - 1) // months * months - past * months
        return date(index // 12, index % 12 + 1, 1)
    return _ALL_TIME


class StatsService:
    """The rank transactions that read and update stats."""

    def __init__(self, store: StatStore, personas: PersonaStore, hosting: Hosting) -> None:
        """Serve stats from a store.

        Args:
            store: Where stats are kept.
            personas: Where the personas that own stats are found.
            hosting: Where the games are that dedicated servers host.
        """
        self._store = store
        self._personas = personas
        self._hosting = hosting

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("rank", "GetStats"): Route(GetStats, self.get_stats),
            ("rank", "GetStatsForOwners"): Route(GetStatsForOwners, self.get_stats_for_owners),
            ("rank", "UpdateStats"): Route(UpdateStats, self.update_stats),
        }

    async def get_stats(self, connection: FeslConnection, request: GetStats) -> GetStatsAnswer:
        """Return one owner's stats, as a dedicated server loads a joining player's.

        Args:
            connection: The FESL connection the request came on.
            request: The owner, period and keys.
        """
        game = game_of(connection)
        owner = owner_of(connection, request.owner, request.owner_type)
        check_size(1, request.keys)
        found = await self._store.values(
            partition_of(game),
            [owner],
            request.keys,
            bucket_of(request.period_id, request.period_past),
        )
        return GetStatsAnswer(stats=[stat_of(key, found.get((owner, key))) for key in request.keys])

    async def get_stats_for_owners(
        self, connection: FeslConnection, request: GetStatsForOwners
    ) -> GetStatsForOwnersAnswer:
        """Return several owners' stats, every owner with every key in the same order.

        Args:
            connection: The FESL connection the request came on.
            request: The owners, period and keys.
        """
        game = game_of(connection)
        owners = [Owner(owner.owner_type, owner.owner_id) for owner in request.owners]
        check_size(len(owners), request.keys)
        found = await self._store.values(
            partition_of(game),
            owners,
            request.keys,
            bucket_of(request.period_id, request.period_past),
        )
        return GetStatsForOwnersAnswer(
            stats=[
                OwnerStats(
                    owner_id=owner.id,
                    owner_type=owner.type,
                    stats=[stat_of(key, found.get((owner, key))) for key in request.keys],
                )
                for owner in owners
            ]
        )

    async def update_stats(self, connection: FeslConnection, request: UpdateStats) -> Message:
        """Update stats as a dedicated server reports its players' rounds, all or nothing.

        Only the game's dedicated servers write, and only while they host a game players see:
        players' stats come from the rounds they play, and a client could write any number.

        Args:
            connection: The FESL connection the request came on.
            request: Each owner's updates.

        Raises:
            TransactionError: When the session is not a dedicated server's that hosts such a
                game, an owner is not a persona of the game or the game, or the request is too
                large.
        """
        game = game_of(connection)
        account = connection.session.account
        if account is None or not account.server:
            raise TransactionError(PARAMETERS_ERROR)
        if not await self._hosting.admits(connection.session):
            raise TransactionError(PARAMETERS_ERROR)
        day = _today()
        changes: list[Change] = []
        for updates in request.owners:
            owner = await self._writable(connection, game, updates)
            changes.extend(
                Change(
                    owner=owner,
                    key=update.key,
                    bucket=Bucket(period, period_start(period, day)),
                    kind=update.kind,
                    value=update.value,
                    text=update.text,
                )
                for update in updates.stats
                for period in dict.fromkeys(update.periods)
            )
        if len(changes) > MAXIMUM_ENTRIES:
            raise TransactionError(PARAMETERS_ERROR)
        await self._store.apply(partition_of(game), changes)
        return Message()

    async def _writable(
        self, connection: FeslConnection, game: Game, updates: OwnerUpdates
    ) -> Owner:
        owner = owner_of(connection, updates.owner, updates.owner_type)
        if owner.type == GAME_WIDE:
            return owner
        if owner.type != PERSONA:
            raise TransactionError(PARAMETERS_ERROR)
        persona = await self._personas.find_by_id(owner.id)
        if persona is None or persona.namespace != game.namespace:
            raise TransactionError(UNKNOWN_PERSONA)
        return owner


def game_of(connection: FeslConnection) -> Game:
    """Return the game of a logged-in session.

    Args:
        connection: The FESL connection a request came on.

    Raises:
        TransactionError: When the session has not logged in.
    """
    if connection.session.account is None or connection.game is None:
        raise TransactionError(NOT_LOGGED_IN)
    return connection.game


def owner_of(connection: FeslConnection, owner_id: int, owner_type: int) -> Owner:
    """Return the owner a request names, or the session's persona when it names none.

    Args:
        connection: The FESL connection the request came on.
        owner_id: The owner's id, 0 when left out.
        owner_type: The owner's type.

    Raises:
        TransactionError: When the request names no owner and the session plays no persona.
    """
    if owner_id:
        return Owner(owner_type, owner_id)
    if connection.session.persona is None:
        raise TransactionError(NOT_LOGGED_IN)
    return Owner(PERSONA, connection.session.persona.id)


def check_size(owners: int, keys: Sequence[str]) -> None:
    """Refuse a request whose answer would hold too many stats.

    Args:
        owners: How many owners the answer holds.
        keys: The keys each of them gets.

    Raises:
        TransactionError: When owners times keys exceed the bound.
    """
    if owners * len(keys) > MAXIMUM_ENTRIES:
        raise TransactionError(PARAMETERS_ERROR)


def partition_of(game: Game) -> str:
    """Return the partition that holds a game's stats, written `/domain/subDomain`.

    Args:
        game: The game.
    """
    return f"/{game.partition.domain}/{game.partition.sub_domain}"


def _today() -> date:
    return datetime.now(UTC).date()


def bucket_of(period: Period, past: int) -> Bucket:
    """Return a period that a request names, counted back from the current one.

    Args:
        period: The kind of period.
        past: How many periods back.
    """
    return Bucket(period, period_start(period, _today(), past))


def stat_of(key: str, found: Value | None) -> Stat:
    """Return a stat as the game reads it, 0 when never stored.

    Args:
        key: The key the game asked for.
        found: What is stored, if anything.
    """
    if found is None:
        return Stat(key=key, value=0.0)
    return Stat(key=key, value=found.value, text=found.text or None)
