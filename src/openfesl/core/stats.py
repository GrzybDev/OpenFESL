from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game
from openfesl.core.personas import NOT_LOGGED_IN, UNKNOWN_PERSONA, PersonaStore
from openfesl.core.trust import ServerTrust
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
    StatUpdate,
    UpdateStats,
    UpdateType,
)

# For annotations only, since the check of players' own writes reads stats through this module.
if TYPE_CHECKING:
    from openfesl.core.hero_items import OwnWrites

PERSONA = 1
# The SDK's owner type for values of the whole game, which dedicated servers only add to.
GAME_WIDE = 11
# The server's own owner type for the stats of an account, which a game names by a persona's type
# and the account's id; no type of the SDK's for accounts is known.
ACCOUNT = -1

# Bounds one request's answer or writes; a dedicated server asks for its game's whole template.
MAXIMUM_ENTRIES = 16384

_MONDAY = date(1970, 1, 5)
_ALL_TIME = date(1970, 1, 1)
_DAYS = {Period.DAILY: 1, Period.WEEKLY: 7, Period.BIWEEKLY: 14}
_MONTHS = {Period.MONTHLY: 1, Period.QUARTERLY: 3, Period.YEARLY: 12}


@dataclass(frozen=True, slots=True)
class Owner:
    """Whose stats: a persona, an account, the whole game or another owner type the SDK knows.

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
        text: The text to keep; none keeps the stored text.
    """

    owner: Owner
    key: str
    bucket: Bucket
    kind: UpdateType
    value: float
    text: str | None


@dataclass(frozen=True, slots=True)
class LevelPoints:
    """The points that a game's personas earn with their level.

    Attributes:
        every: A persona earns a point on each level that is a multiple of this.
        game: The game's data-file name.
        currency: The points' currency, which each persona holds on its own.
        allowance: What a wallet of the points opens with.
    """

    every: int
    game: str
    currency: str
    allowance: int


@dataclass(frozen=True, slots=True)
class Levels:
    """How a game keeps its personas' levels, and the points they earn with them.

    Attributes:
        key: The key of the stat that holds a persona's level.
        highest: The highest level, as the lowest is 1.
        points: The points that personas earn with their level; none for a game whose personas
            earn none.
    """

    key: str
    highest: int
    points: LevelPoints | None


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

    async def owned(self, partition: str, owner: Owner, bucket: Bucket) -> dict[str, Value]:
        """Return every stat that an owner has stored in a period, by key.

        Args:
            partition: The game's partition.
            owner: Whose stats.
            bucket: The period.
        """

    async def apply(
        self, partition: str, changes: Sequence[Change], levels: Levels | None = None
    ) -> None:
        """Apply updates in their order, all of them or none, with the points a level earns.

        A value never stored counts as 0, which is the game's own default: games send their
        values as offsets from the defaults in their own data.

        Args:
            partition: The game's partition.
            changes: The updates.
            levels: How the personas' levels are kept: each level an update changes stays
                within its bounds, and each persona whose level changes is credited at once the
                points that its level earns and never earned before, so that none is credited
                twice or taken back; none for a game whose personas have no levels.
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

    def __init__(
        self, store: StatStore, personas: PersonaStore, trust: ServerTrust, own: OwnWrites
    ) -> None:
        """Serve stats from a store.

        Args:
            store: Where stats are kept.
            personas: Where the personas that own stats are found.
            trust: Which dedicated servers host games that players see, and who plays in them.
            own: What players' own games may write of their heroes' and accounts' stats.
        """
        self._store = store
        self._personas = personas
        self._trust = trust
        self._own = own

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("rank", "GetStats"): Route(GetStats, self.get_stats),
            ("rank", "GetStatsForOwners"): Route(GetStatsForOwners, self.get_stats_for_owners),
            ("rank", "UpdateStats"): Route(UpdateStats, self.update_stats),
        }

    async def get_stats(self, connection: FeslConnection, request: GetStats) -> GetStatsAnswer:
        """Return one owner's stats, as a dedicated server loads a joining player's.

        A dedicated server that logs in with a server account gets them only while it hosts a
        game players see, as it writes; one that logs in as a player reads as players do.

        Args:
            connection: The FESL connection the request came on.
            request: The owner, period and keys.

        Raises:
            TransactionError: When a server account that hosts no such game asks, or the
                request names no owner while the session plays no persona.
        """
        game = game_of(connection)
        account = connection.session.account
        # Refused, a server uploads none of the player's stats that session; given them, it
        # would retry every upload until it quits.
        if account and account.server and not await self._trust.admits(connection.session):
            raise TransactionError(PARAMETERS_ERROR)
        owners = stat_owners(connection, game, request.owner, request.owner_type, request.keys)
        check_size(1, request.keys)
        found = await self._store.values(
            partition_of(game),
            list(dict.fromkeys(owners.values())),
            request.keys,
            bucket_of(request.period_id, request.period_past),
        )
        return GetStatsAnswer(
            stats=[stat_of(key, found.get((owners[key], key))) for key in request.keys]
        )

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
        kept = {
            (owner, key): kept_under(game, owner, key) for owner in owners for key in request.keys
        }
        found = await self._store.values(
            partition_of(game),
            list(dict.fromkeys(kept.values())),
            request.keys,
            bucket_of(request.period_id, request.period_past),
        )
        return GetStatsForOwnersAnswer(
            stats=[
                OwnerStats(
                    owner_id=owner.id,
                    owner_type=owner.type,
                    stats=[
                        stat_of(key, found.get((kept[owner, key], key))) for key in request.keys
                    ],
                )
                for owner in owners
            ]
        )

    async def update_stats(self, connection: FeslConnection, request: UpdateStats) -> Message:
        """Update stats as servers report players' rounds or games keep their own, all or nothing.

        Players' stats come from the rounds they play, which the game's dedicated servers write
        while they host a game players see, and a client could write any number. A server that
        logs in as a player writes only for the personas it keeps, and any server writes the
        stats of an account only for an account whose persona it keeps. A player's own game
        writes only what the game keeps itself of its hero and account.

        Args:
            connection: The FESL connection the request came on.
            request: Each owner's updates.

        Raises:
            TransactionError: When the session is not a dedicated server's that hosts such a
                game and its game may not write the request, an owner is not a persona of the
                game, the game or a player the server may write for, or the request is too
                large.
        """
        game = game_of(connection)
        trusted = await self._trust.serves(connection.session)
        if trusted:
            owned = [await self._writable(connection, game, updates) for updates in request.owners]
        else:
            owned = await self._own.owners(connection, game, request)
        day = _today()
        changes = [
            Change(
                owner=owners[update.key],
                key=update.key,
                bucket=Bucket(period, period_start(period, day)),
                kind=update.kind,
                value=update.value,
                text=_text(game, update, trusted),
            )
            for updates, owners in zip(request.owners, owned, strict=True)
            for update in updates.stats
            for period in dict.fromkeys(update.periods)
        ]
        if len(changes) > MAXIMUM_ENTRIES:
            raise TransactionError(PARAMETERS_ERROR)
        await self._store.apply(partition_of(game), changes, _levels(game))
        return Message()

    async def _writable(
        self, connection: FeslConnection, game: Game, updates: OwnerUpdates
    ) -> dict[str, Owner]:
        named = owner_of(connection, updates.owner, updates.owner_type)
        if named.type not in (PERSONA, GAME_WIDE):
            raise TransactionError(PARAMETERS_ERROR)
        keys = [update.key for update in updates.stats]
        owners = stat_owners(connection, game, updates.owner, updates.owner_type, keys)
        # An owner named without stats is checked too.
        for owner in dict.fromkeys(owners.values()) or [named]:
            await self._check_writable(connection, game, owner)
        return owners

    async def _check_writable(self, connection: FeslConnection, game: Game, owner: Owner) -> None:
        session = connection.session
        if owner.type == PERSONA:
            persona = await self._personas.find_by_id(owner.id)
            if persona is None or persona.namespace != game.namespace:
                raise TransactionError(UNKNOWN_PERSONA)
            # Only dedicated servers have the game's server account; a server that logs in as a
            # player could be any player's game, so it writes only for the personas it keeps.
            account = session.account
            if (account and account.server) or await self._trust.keeps_persona(session, owner.id):
                return
            raise TransactionError(PARAMETERS_ERROR)
        if owner.type == ACCOUNT and not await self._trust.keeps_account(session, owner.id):
            raise TransactionError(PARAMETERS_ERROR)


def _text(game: Game, update: StatUpdate, trusted: bool) -> str | None:
    # A player's game writes an emptied list of items as an empty text, which clears the stored
    # one, while servers send an empty text with every number they write, which keeps it.
    stat = game.heroes.written_stat(update.key) if game.heroes and not trusted else None
    return update.text if stat and stat.listed else update.text or None


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


def kept_under(game: Game, owner: Owner, key: str) -> Owner:
    """Return the owner that a stat is kept under, for the owner a request names.

    A game names an account by a persona's owner type, so the stats of accounts are kept under
    the account that has the id named.

    Args:
        game: The game.
        owner: The owner the request names.
        key: The stat's key.
    """
    if owner.type == PERSONA and game.owned_by_account(key):
        return Owner(ACCOUNT, owner.id)
    return owner


def stat_owners(
    connection: FeslConnection, game: Game, owner_id: int, owner_type: int, keys: Sequence[str]
) -> dict[str, Owner]:
    """Return the owner that each of a request's stats is kept under, by key.

    A request that names no owner is about the session: its persona, and its account for the
    stats of accounts.

    Args:
        connection: The FESL connection the request came on.
        game: The session's game.
        owner_id: The owner's id, 0 when left out.
        owner_type: The owner's type.
        keys: The stats' keys.

    Raises:
        TransactionError: When the request names no owner and the session plays no persona.
    """
    owner = owner_of(connection, owner_id, owner_type)
    account = connection.session.account
    if not owner_id and account is not None:
        mine = Owner(ACCOUNT, account.id)
        return {key: mine if game.owned_by_account(key) else owner for key in keys}
    return {key: kept_under(game, owner, key) for key in keys}


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


def _levels(game: Game) -> Levels | None:
    rules = game.heroes
    if rules is None:
        return None
    points = None
    if rules.points_every is not None:
        allowance = game.currencies[rules.points].allowance
        points = LevelPoints(rules.points_every, game.id, rules.points, allowance)
    return Levels(rules.stats.level, rules.highest_level, points)


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
