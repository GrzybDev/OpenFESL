from pydantic import Field

from openfesl.protocol.message import Message


class PartitionName(Message):
    """A partition written `/domain/subDomain`, as PlayNow names the one to search."""

    partition: str


class Searcher(Message):
    """A player that searches, with the search's properties without their braces.

    The first player carries the search kind (`sessionType`), the filters (`filter-<name>`,
    mapped to a game attribute by `filterToGame-<name>`) and the preferences (`pref-<name>`,
    mapped by `prefToGame-<name>`).
    """

    owner_id: int = 0
    owner_type: int = 1
    props: dict[str, str] = Field(default_factory=dict)


class Start(Message):
    """Start: a player asks for games to join."""

    partition: PartitionName
    players: list[Searcher] = Field(default_factory=list)


class SessionId(Message):
    """A PlayNow search, which a Status names together with its partition."""

    id: int
    partition: str


class StartAnswer(Message):
    id: SessionId


class Cancel(Message):
    """Cancel: the player stops a search."""

    id: SessionId | None = None


class FoundGame(Message):
    """A game a search found, which the player's game then looks up in Theater."""

    lid: int
    gid: int
    fit: float


class Outcome(Message):
    """What a search found: games to join or list, or why there are none."""

    result_type: str = Field(alias="{resultType}")
    games: list[FoundGame] | None = Field(None, alias="{games}")
    avg_fit: float | None = Field(None, alias="{avgFit}")
    available_server_count: int | None = Field(None, alias="{availableServerCount}")


class Status(Message):
    """Status: the state of a search, pushed once it is complete."""

    id: SessionId
    session_state: str
    props: Outcome
