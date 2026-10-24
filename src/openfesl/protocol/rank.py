from enum import IntEnum
from typing import Annotated

from pydantic import BeforeValidator, Field, FiniteFloat

from openfesl.protocol.message import Int32, Int64, Message, Text

# The SDK reads keys into 64 bytes and texts into 256, each with its NUL.
Key = Annotated[Text, Field(min_length=1, max_length=63)]
StatText = Annotated[Text, Field(max_length=255)]


class Period(IntEnum):
    """A statistics period as the SDK numbers it on the wire."""

    ALL_TIME = 0
    DAILY = 1
    WEEKLY = 2
    BIWEEKLY = 3
    MONTHLY = 4
    QUARTERLY = 5
    YEARLY = 6


class UpdateType(IntEnum):
    """How an update combines with the value kept so far."""

    SET = 0
    MAX = 1
    MIN = 2
    ADD = 3
    SUBTRACT = 4


def _periods(value: object) -> object:
    return [part for part in value.split(",") if part] if isinstance(value, str) else value


# A comma list such as `0,2`; an empty one updates no period.
Periods = Annotated[tuple[Period, ...], BeforeValidator(_periods)]


class StatOwner(Message):
    owner_id: Int64
    owner_type: Int32 = 1


class GetStats(Message):
    """Stats of one owner, of the session's persona when the owner is left out."""

    owner: Int64 = 0
    owner_type: Int32 = 1
    period_id: Period = Period.ALL_TIME
    period_past: int = Field(0, ge=0, le=1000)
    keys: list[Key] = Field(default_factory=list)


class Stat(Message):
    key: str
    value: float
    text: str | None = None


class GetStatsAnswer(Message):
    """Every requested key in the order asked; a value never stored is 0."""

    stats: list[Stat]


class GetStatsForOwners(Message):
    owners: list[StatOwner] = Field(default_factory=list)
    period_id: Period = Period.ALL_TIME
    period_past: int = Field(0, ge=0, le=1000)
    keys: list[Key] = Field(default_factory=list)


class OwnerStats(Message):
    owner_id: int
    owner_type: int
    stats: list[Stat]


class GetStatsForOwnersAnswer(Message):
    """Each owner's stats; the game takes the inner count from the first owner only."""

    stats: list[OwnerStats]


class StatUpdate(Message):
    kind: UpdateType = Field(alias="ut")
    key: Key = Field(alias="k")
    value: FiniteFloat = Field(alias="v")
    text: StatText = Field("", alias="t")
    periods: Periods = Field((Period.ALL_TIME,), alias="pt")


class OwnerUpdates(Message):
    """Updates of one owner; the owner is left out for the session's own persona."""

    owner: Int64 = Field(0, alias="o")
    owner_type: Int32 = Field(1, alias="ot")
    stats: list[StatUpdate] = Field(default_factory=list, alias="s")


class UpdateStats(Message):
    owners: list[OwnerUpdates] = Field(default_factory=list, alias="u")
