from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from openfesl.core.stats import Bucket

# What a leaderboard is sorted by: a stat the game data sums up, or kills per death.
KD = "kd"


@dataclass(frozen=True, slots=True)
class Line:
    """A player's line on a game's leaderboard.

    Attributes:
        persona_id: The player's persona.
        name: The persona's name.
        place: The player's position among every player with stats, from 1; players that tie
            are ordered by persona.
        values: The player's stats by their names, 0 for those never stored.
    """

    persona_id: int
    name: str
    place: int
    values: Mapping[str, float]

    @property
    def kd(self) -> float:
        """The kills per death, or the kills while the player never died."""
        return kd(self.values.get("kills", 0.0), self.values.get("deaths", 0.0))


def kd(kills: float, deaths: float) -> float:
    """Return the kills per death, as the leaderboard sorts by them.

    Args:
        kills: The kills.
        deaths: The deaths; with none, the kills count as they are.
    """
    return kills / deaths if deaths > 0 else kills


class RankingStore(Protocol):
    """Where the stats of a game's players are kept, summed up and ordered for its leaderboard.

    The leaderboard holds the enabled personas with any of the summed stats stored.
    """

    async def lines(
        self,
        partition: str,
        sums: Mapping[str, Sequence[str]],
        order: str,
        bucket: Bucket,
        among: Collection[int] | None,
        window: slice,
    ) -> list[Line]:
        """Return players' lines in their order, best first.

        Args:
            partition: The game's partition.
            sums: The stats to show by their names, each with the keys whose values it adds up.
            order: The name of the stat the lines are sorted by, highest first, or `KD`.
            bucket: The period.
            among: The personas whose lines to return, keeping everyone's positions; every
                player's when None.
            window: Which of the returned lines, counted from 0.
        """

    async def count(
        self,
        partition: str,
        keys: Sequence[str],
        bucket: Bucket,
        among: Collection[int] | None,
    ) -> int:
        """Return how many players are on the leaderboard.

        Args:
            partition: The game's partition.
            keys: Every key the leaderboard sums up.
            bucket: The period.
            among: Count only these personas; every player when None.
        """

    async def find(
        self,
        partition: str,
        sums: Mapping[str, Sequence[str]],
        order: str,
        bucket: Bucket,
        name: str,
    ) -> Line | None:
        """Return the line of a player found by name: called it, or else holding it, best first.

        Names match regardless of case.

        Args:
            partition: The game's partition.
            sums: The stats to show by their names, each with the keys whose values it adds up.
            order: The name of the stat the lines are sorted by, highest first, or `KD`.
            bucket: The period.
            name: The name or part of it.
        """
