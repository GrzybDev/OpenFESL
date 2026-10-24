from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Protocol

from openfesl.core.fesl import FeslConnection, Route
from openfesl.core.stats import (
    PERSONA,
    Bucket,
    Owner,
    StatStore,
    bucket_of,
    check_size,
    game_of,
    owner_of,
    partition_of,
    period_start,
    stat_of,
)
from openfesl.protocol.rank import (
    GetDateRange,
    GetDateRangeAnswer,
    GetRankedStats,
    GetRankedStatsAnswer,
    GetRankedStatsForOwners,
    GetRankedStatsForOwnersAnswer,
    GetTopN,
    GetTopNAnswer,
    Leader,
    OwnerRankedStats,
    Period,
    RankedStat,
    RankOrder,
    Stat,
)

# The longest leaderboard window one request gets.
MAXIMUM_STANDINGS = 256


@dataclass(frozen=True, slots=True)
class Ranked:
    """A stored stat and its owner's rank among every owner of its type, 1 for the best.

    Ties share a rank and the next owner's rank counts every owner before it. Disabled
    personas are left out.
    """

    value: float
    text: str
    rank: int


@dataclass(frozen=True, slots=True)
class Standing:
    """An owner's place on a leaderboard.

    Attributes:
        owner_id: The owner's id.
        name: The persona's name; empty for owners that are not personas.
        value: The ranked stat's value.
        text: The ranked stat's text.
        rank: The owner's rank, 1 for the best.
    """

    owner_id: int
    name: str
    value: float
    text: str
    rank: int


class LeaderboardStore(StatStore, Protocol):
    """Where stats are kept, ranked across every owner of a type."""

    async def ranked(
        self,
        partition: str,
        owners: Sequence[Owner],
        keys: Sequence[str],
        order: RankOrder,
        bucket: Bucket,
    ) -> dict[tuple[Owner, str], Ranked]:
        """Return the stored stats of owners with their ranks, leaving out those never stored.

        Args:
            partition: The game's partition.
            owners: Whose stats.
            keys: The stats' keys.
            order: Which values rank first.
            bucket: The period.
        """

    async def standings(
        self,
        partition: str,
        owner_type: int,
        key: str,
        order: RankOrder,
        bucket: Bucket,
        ranks: range,
    ) -> list[Standing]:
        """Return the owners whose places fall within a window, best first.

        Every owner has a place of its own, so that pages of a leaderboard hold each owner
        once, while owners that tie share their rank. Disabled personas are left out.

        Args:
            partition: The game's partition.
            owner_type: The type of owners ranked.
            key: The ranked stat's key.
            order: Which values rank first.
            bucket: The period.
            ranks: The window of places.
        """


class LeaderboardService:
    """The rank transactions that rank stats and list leaderboards."""

    def __init__(self, store: LeaderboardStore) -> None:
        """Serve leaderboards from a store.

        Args:
            store: Where stats are kept.
        """
        self._store = store

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("rank", "GetRankedStats"): Route(GetRankedStats, self.get_ranked_stats),
            ("rank", "GetRankedStatsForOwners"): Route(
                GetRankedStatsForOwners, self.get_ranked_stats_for_owners
            ),
            ("rank", "GetTopN"): Route(GetTopN, self.top_n),
            ("rank", "GetTopNAndMe"): Route(GetTopN, self.top_n),
            ("rank", "GetTopNAndStats"): Route(GetTopN, self.top_n_and_stats),
            ("rank", "GetDateRange"): Route(GetDateRange, self.date_range),
        }

    async def get_ranked_stats(
        self, connection: FeslConnection, request: GetRankedStats
    ) -> GetRankedStatsAnswer:
        """Return one owner's stats with its ranks, of the session's persona by default.

        Args:
            connection: The FESL connection the request came on.
            request: The owner, period, keys and their orders.
        """
        owner = owner_of(connection, request.owner, request.owner_type)
        found = await self._ranked(
            connection,
            [owner],
            request.keys,
            request.rank_orders,
            request.period_id,
            request.period_past,
        )
        return GetRankedStatsAnswer(
            stats=[_ranked_stat(key, found.get((owner, key))) for key in request.keys]
        )

    async def get_ranked_stats_for_owners(
        self, connection: FeslConnection, request: GetRankedStatsForOwners
    ) -> GetRankedStatsForOwnersAnswer:
        """Return several owners' ranked stats, every owner with every key in the same order.

        Args:
            connection: The FESL connection the request came on.
            request: The owners, period, keys and their orders.
        """
        owners = [Owner(owner.owner_type, owner.owner_id) for owner in request.owners]
        found = await self._ranked(
            connection,
            owners,
            request.keys,
            request.rank_orders,
            request.period_id,
            request.period_past,
        )
        return GetRankedStatsForOwnersAnswer(
            ranked_stats=[
                OwnerRankedStats(
                    owner_id=owner.id,
                    owner_type=owner.type,
                    ranked_stats=[
                        _ranked_stat(key, found.get((owner, key))) for key in request.keys
                    ],
                )
                for owner in owners
            ]
        )

    async def top_n(self, connection: FeslConnection, request: GetTopN) -> GetTopNAnswer:
        """Return a leaderboard window, and the caller's own place when it asks and is not in it.

        Args:
            connection: The FESL connection the request came on.
            request: The ranked stat, period, order and window.
        """
        return GetTopNAnswer(
            stats=[_leader(standing) for standing in await self._standings(connection, request)]
        )

    async def top_n_and_stats(self, connection: FeslConnection, request: GetTopN) -> GetTopNAnswer:
        """Return a leaderboard window with more of each owner's stats.

        Args:
            connection: The FESL connection the request came on.
            request: The ranked stat, period, order, window and the further keys.
        """
        standings = await self._standings(connection, request)
        check_size(len(standings), request.keys)
        owners = [Owner(request.owner_type, standing.owner_id) for standing in standings]
        found = await self._store.values(
            partition_of(game_of(connection)),
            owners,
            request.keys,
            bucket_of(request.period_id, request.period_past),
        )
        return GetTopNAnswer(
            stats=[
                _leader(standing, [stat_of(key, found.get((owner, key))) for key in request.keys])
                for owner, standing in zip(owners, standings, strict=True)
            ]
        )

    async def date_range(
        self, connection: FeslConnection, request: GetDateRange
    ) -> GetDateRangeAnswer:
        """Return when the current period started and ends; all time ends now.

        Args:
            connection: The FESL connection the request came on.
            request: The period.
        """
        game_of(connection)
        now = datetime.now(UTC).replace(microsecond=0)
        start = datetime.combine(period_start(request.period_id, now.date()), time(), UTC)
        if request.period_id == Period.ALL_TIME:
            return GetDateRangeAnswer(start_date=start, end_date=now)
        following = period_start(request.period_id, now.date(), -1)
        end = datetime.combine(following, time(), UTC) - timedelta(seconds=1)
        return GetDateRangeAnswer(start_date=start, end_date=end)

    async def _ranked(
        self,
        connection: FeslConnection,
        owners: Sequence[Owner],
        keys: Sequence[str],
        orders: Sequence[RankOrder],
        period: Period,
        past: int,
    ) -> dict[tuple[Owner, str], Ranked]:
        partition = partition_of(game_of(connection))
        check_size(len(owners), keys)
        by_order: dict[RankOrder, list[str]] = {}
        for index, key in enumerate(keys):
            order = orders[index] if index < len(orders) else RankOrder.DESCENDING
            by_order.setdefault(order, []).append(key)
        found: dict[tuple[Owner, str], Ranked] = {}
        for order, ordered in by_order.items():
            found |= await self._store.ranked(
                partition, owners, ordered, order, bucket_of(period, past)
            )
        return found

    async def _standings(self, connection: FeslConnection, request: GetTopN) -> list[Standing]:
        partition = partition_of(game_of(connection))
        bucket = bucket_of(request.period_id, request.period_past)
        first = max(request.min_rank, 1)
        ranks = range(first, min(request.max_rank, first + MAXIMUM_STANDINGS - 1) + 1)
        standings = (
            await self._store.standings(
                partition, request.owner_type, request.key, request.rank_order, bucket, ranks
            )
            if ranks
            else []
        )
        persona = connection.session.persona
        if (
            not request.include_user
            or persona is None
            or request.owner_type != PERSONA
            or any(standing.owner_id == persona.id for standing in standings)
        ):
            return standings
        me = Owner(PERSONA, persona.id)
        ranked = await self._store.ranked(
            partition, [me], [request.key], request.rank_order, bucket
        )
        if (mine := ranked.get((me, request.key))) is not None:
            standings.append(Standing(persona.id, persona.name, mine.value, mine.text, mine.rank))
        return standings


def _ranked_stat(key: str, found: Ranked | None) -> RankedStat:
    if found is None:
        return RankedStat(key=key, value=0.0, rank=0)
    return RankedStat(key=key, value=found.value, text=found.text or None, rank=found.rank)


def _leader(standing: Standing, add_stats: list[Stat] | None = None) -> Leader:
    return Leader(
        owner=standing.owner_id,
        name=standing.name,
        rank=standing.rank,
        value=standing.value,
        text=standing.text or None,
        add_stats=add_stats,
    )
