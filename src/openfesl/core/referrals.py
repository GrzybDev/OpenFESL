from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openfesl.core.accounts import Account
from openfesl.core.admins import Admin
from openfesl.core.games import Game


@dataclass(frozen=True, slots=True)
class Reach:
    """A referral step's goal as it was first seen reached.

    Attributes:
        at: When.
        hero: The name of the recruit's hero that reached it.
        level: The hero's level then.
    """

    at: datetime
    hero: str
    level: int


@dataclass(frozen=True, slots=True)
class Leader:
    """The hero of a recruit with the highest level, the first made among equals.

    Attributes:
        persona_id: Its persona.
        name: Its name.
        level: Its level.
    """

    persona_id: int
    name: str
    level: int


@dataclass(frozen=True, slots=True)
class Recruit:
    """An account that a player's personal link brought, recorded when it was made.

    Attributes:
        account: The recruit's account.
        recruiter: The account whose link it was.
        joined: When the recruit signed up.
        reached: When each step's goal was first seen reached, by step.
        leader: The recruit's hero with the highest level; none before its first hero.
    """

    account: Account
    recruiter: Account
    joined: datetime
    reached: Mapping[str, Reach]
    leader: Leader | None


@dataclass(frozen=True, slots=True)
class ReferralCounts:
    """How Recruit a Friend of a game goes, as the admin panel counts it.

    Attributes:
        month: The recruits that signed up this month.
        all: Every recruit.
        claimed: The steps' rewards claimed this month, by recruiters and recruits.
        waiting: The rewards of steps reached that their accounts have not claimed yet.
    """

    month: int
    all: int
    claimed: int
    waiting: int


class ReferralStore(Protocol):
    """Where the recruits of personal links are kept, with the steps they reached."""

    async def recruits(self, game: Game, recruiter_id: int) -> list[Recruit]:
        """Return the recruits of an account in a game, the newest first.

        Args:
            game: The game, whose heroes count.
            recruiter_id: The recruiter's account.
        """

    async def recruited(self, game: Game, recruit_id: int) -> Recruit | None:
        """Return an account as a recruit in a game, if a personal link brought it.

        Args:
            game: The game, whose heroes count.
            recruit_id: The account.
        """

    async def reach(self, game: str, recruit_id: int, step: str, reach: Reach) -> None:
        """Record that a recruit's hero reached a step's goal, only the first time it is seen.

        Args:
            game: The game's data-file name.
            recruit_id: The recruit's account.
            step: The step's name.
            reach: When, by which hero and at which level.
        """

    async def remove(self, game: Game, recruit_id: int, note: str, admin: Admin) -> Recruit | None:
        """Take an account out of the recruits of its recruiter and record why; claims stay.

        Args:
            game: The game.
            recruit_id: The recruit's account.
            note: Why, as the admin wrote it.
            admin: The admin who removed it.

        Returns:
            The recruit as it was; none when it was no recruit, also once removed meanwhile.
        """

    async def counts(self, game: str, since: datetime) -> ReferralCounts:
        """Return how many recruits signed up and how many rewards were claimed and wait.

        Args:
            game: The game's data-file name.
            since: The start of the month.
        """
