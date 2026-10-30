from asyncio import Lock
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.stats import Bucket

# How long a board's ranking stands before the next look at it ranks the heroes again, as the
# 2012 site updated its boards every hour. Ranking reads every hero's stats of the period.
FRESH = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Ranking:
    """How a leaderboard of a game's heroes ranks them and which heroes it lists.

    Attributes:
        keys: The key of the stat that ranks the heroes, highest first, or the keys of a stat and
            of the one it is divided by; without the second, the first counts alone.
        time: The key of the time played, in seconds.
        least: The time a hero plays in the period before the board lists it.
        look: The keys of a hero's faction, class and level, kept for all time.
        places: How many of the best heroes the board lists.
        within: The index in look of the faction or class that a board of one faction or class
            lists, with the number a hero holds for it; none for a board of every hero.
    """

    keys: tuple[str, ...]
    time: str
    least: float
    look: tuple[str, str, str]
    places: int
    within: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class Placed:
    """A hero's line on a leaderboard.

    Attributes:
        hero_id: The hero's persona.
        name: The hero's name.
        account_id: The account it belongs to.
        player: The account's display name.
        place: Its place, from 1; heroes that tie are ordered by persona.
        value: The value it is ranked by.
        look: The numbers it holds for its faction, class and level, if it has them.
    """

    hero_id: int
    name: str
    account_id: int
    player: str
    place: int
    value: float
    look: tuple[int | None, int | None, int | None]


@dataclass(frozen=True, slots=True)
class Standings:
    """A leaderboard of heroes as it was last ranked.

    Attributes:
        lines: The heroes' lines, best first.
        ranked: When the heroes were ranked.
    """

    lines: Sequence[Placed]
    ranked: datetime


class HeroBoardStore(Protocol):
    """Where the leaderboards of a game's heroes are ranked from its stats.

    A board lists the heroes in play of accounts that others see, as the player search finds
    them: in use, named and players' own.
    """

    async def ranking(
        self, partition: str, namespace: str, ranking: Ranking, bucket: Bucket
    ) -> list[Placed]:
        """Return the lines of the best heroes on a leaderboard, as many as it has places.

        Args:
            partition: The game's partition.
            namespace: The namespace of the game's heroes.
            ranking: How the board ranks heroes and which it lists.
            bucket: The period it ranks.
        """


class HeroBoards:
    """The leaderboards of games' heroes, each kept as ranked for FRESH and then ranked again.

    The pages that show a board within that time share one ranking, so that a page costs no
    reading of every hero's stats. The admin taking an account or a hero out of play, or back
    into it, has each board ranked again on its next look.
    """

    def __init__(self, store: HeroBoardStore) -> None:
        """Rank the boards from the heroes' stats.

        Args:
            store: Ranks the boards.
        """
        self._store = store
        self._boards: dict[tuple[str, str, Ranking, Bucket], Standings] = {}
        self._locks: dict[tuple[str, str, Ranking, Bucket], Lock] = {}
        # When the admin last changed whose heroes the boards list; rankings begun before then
        # are ranked again.
        self._changed = datetime.min.replace(tzinfo=UTC)

    def follow(self, feed: AdminFeed) -> None:
        """Rank the boards again on their next look once an account's or hero's state changes.

        Args:
            feed: The admin's actions as the log records them.
        """
        for action in (
            AdminAction.ACCOUNT_STATE,
            AdminAction.DELETION_REQUEST,
            AdminAction.PERSONA_STATE,
        ):
            feed.on(action, self._change)

    async def standings(
        self, partition: str, namespace: str, ranking: Ranking, bucket: Bucket
    ) -> Standings:
        """Return a leaderboard as last ranked, ranking it again first once it is out of date.

        Args:
            partition: The game's partition.
            namespace: The namespace of the game's heroes.
            ranking: How the board ranks heroes and which it lists.
            bucket: The period it ranks.
        """
        board = (partition, namespace, ranking, bucket)
        # One look ranks a board while the others that come meanwhile wait for its ranking.
        async with self._locks.setdefault(board, Lock()):
            now = datetime.now(UTC)
            found = self._boards.get(board)
            if found is None or now - found.ranked >= FRESH or found.ranked <= self._changed:
                found = Standings(await self._store.ranking(*board), now)
                # Only the boards looked at within the time stay in memory.
                self._boards = {
                    kept: standings
                    for kept, standings in self._boards.items()
                    if now - standings.ranked < FRESH
                }
                self._boards[board] = found
                self._locks = {
                    kept: lock
                    for kept, lock in self._locks.items()
                    if kept in self._boards or lock.locked()
                }
            return found

    def _change(self, action: LoggedAction) -> None:
        self._changed = datetime.now(UTC)
