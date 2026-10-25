import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from openfesl.core.census import Activity, Census
from openfesl.core.games import Game

logger = logging.getLogger(__name__)

# How often each game's players online are counted, in seconds.
SAMPLE_INTERVAL = 60.0
# How long the peaks of single hours are kept; the peaks of days are kept for good.
HOURS_KEPT = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class Peak:
    """The most players that were online at once in an hour or a day, from the counts taken.

    Attributes:
        start: When the hour or day began, in UTC.
        at: When that many were counted; the earliest such count.
        online: The players logged in to the game then.
        in_matches: Those among them in a game that a server hosted.
        servers: The games that servers hosted then.
    """

    start: datetime
    at: datetime
    online: int
    in_matches: int
    servers: int

    @property
    def in_menus(self) -> int:
        """The players logged in then who were in no hosted game."""
        return max(self.online - self.in_matches, 0)


class HistoryStore(Protocol):
    """Where the counts of each game's players online are kept, as the peaks of hours and days."""

    async def record(self, game: str, at: datetime, activity: Activity) -> None:
        """Keep a count as the peak of its hour and of its day where it is higher than theirs.

        Args:
            game: The id of the game counted.
            at: When it was counted, in UTC.
            activity: How many played it then.
        """

    async def forget_hours(self, before: datetime) -> None:
        """Drop the peaks of the hours that began before a time.

        Args:
            before: The time.
        """

    async def hours(self, game: str, since: datetime) -> list[Peak]:
        """Return the peaks of a game's hours from one on, oldest first.

        Args:
            game: The id of the game.
            since: The start of the first hour.
        """

    async def days(self, game: str, since: date | None = None) -> list[Peak]:
        """Return the peaks of a game's days, oldest first.

        Args:
            game: The id of the game.
            since: The first day; every day counted when omitted.
        """

    async def best(self, game: str) -> Peak | None:
        """Return the peak of the day with the most players online at once, the first of equals.

        Args:
            game: The id of the game.
        """


class OnlineHistory:
    """Counts each game's players online every minute and keeps the peaks of hours and days."""

    def __init__(self, store: HistoryStore, census: Census, games: Iterable[Game]) -> None:
        """Count the players of some games.

        Args:
            store: Where the peaks are kept.
            census: Counts the players now.
            games: The games counted.
        """
        self._store = store
        self._census = census
        self._games = list(games)

    async def follow(self) -> None:
        """Count the players now and then every SAMPLE_INTERVAL seconds, until cancelled."""
        while True:
            await self.count(datetime.now(UTC))
            await asyncio.sleep(SAMPLE_INTERVAL)

    async def count(self, at: datetime) -> None:
        """Count every game's players and forget the hours kept long enough.

        Args:
            at: The time of the count, in UTC.
        """
        try:
            for game in self._games:
                await self._store.record(game.id, at, self._census.activity(game))
            await self._store.forget_hours(at - HOURS_KEPT)
        except Exception:
            # The database may be away for a moment; the next count tries again.
            logger.exception("Could not record the players online")
