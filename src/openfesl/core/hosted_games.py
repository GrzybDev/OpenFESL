import asyncio
import itertools
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto
from ipaddress import IPv4Address

from openfesl.core.games import Lobby
from openfesl.core.personas import Persona
from openfesl.core.servers import GameServer
from openfesl.core.theater import TheaterConnection


class PlayerState(Enum):
    """How far a player got into a hosted game."""

    QUEUED = auto()
    """It waits in the game's queue for a place."""
    JOINING = auto()
    """The host was asked whether it may join (EGRQ) and has not answered."""
    APPROVED = auto()
    """The host allowed it and it connects to the host (EGEG)."""
    ENTERED = auto()
    """The host reported it in the game (PENT)."""


@dataclass(eq=False)
class Player:
    """A player of a hosted game, from its EGAM until its host reports it left.

    Attributes:
        pid: The number the server gave the player within the game.
        persona: Who plays.
        connection: The player's Theater connection.
        participant: Whether it plays rather than watches.
        address: The player's public IPv4 address.
        port: The public port of the player's game socket.
        internal_address: The player's LAN address, its public one when its game knows none.
        internal_port: The port of the player's game socket on its LAN.
        request: The `R-U-` attributes the player's game sent for the host.
        group: The playgroup the player joins with, if any.
        looked_up: The player its EGAM named the game by, if it did.
        ticket: What the player proves to its host that the server let it in with.
        state: How far it got.
        attributes: The player's `P-` attributes by full key.
        deadline: When the host's answer is due, while the server waits for it.
    """

    pid: int
    persona: Persona
    connection: TheaterConnection
    participant: bool
    address: IPv4Address
    port: int
    internal_address: IPv4Address
    internal_port: int
    request: dict[str, str]
    group: int | None
    looked_up: Persona | None
    ticket: str
    state: PlayerState = PlayerState.QUEUED
    attributes: dict[str, str] = field(default_factory=dict)
    deadline: asyncio.TimerHandle | None = None

    def stop_waiting(self) -> None:
        """Stop waiting for the host's answer about the player."""
        if self.deadline is not None:
            self.deadline.cancel()
            self.deadline = None


@dataclass(eq=False)
class HostedGame:
    """A game a host lists in a lobby, as its CGAM and later updates describe it.

    Attributes:
        lobby: The lobby it is listed in.
        id: Its number, unique on the server.
        host: The Theater connection of its host.
        type: G for a game, P for a playgroup.
        name: Its name.
        address: The host's public IPv4 address.
        port: The host's public game port.
        internal_address: The host's LAN address, when the host knows it.
        internal_port: The host's LAN game port.
        ugid: Its persistent id.
        server: The dedicated server that hosts it; none for a player's own playgroup.
        listed: Whether players see it; games of servers waiting for approval are hidden.
        key: The key the game's traffic is encrypted with, which joining players get too.
        join: Who may join: O open, W wait, C closed.
        max_players: How many participants it takes.
        max_observers: How many observers it takes.
        observers: How many observers it has.
        queue_length: How many players may wait for a place.
        dequeue: Whether the server lets waiting players in as places free; otherwise the
            host picks them itself (DQEG).
        password: Whether joining needs a password.
        version: The game protocol version its players need.
        basic: The `B-U-` attributes by full key.
        details: The `D-` attributes by full key.
        players: Its players that are past the queue, by PID.
        entered: The personas its host reported in the game since it listed it, by id, those
            that left included.
        queue: Its waiting players, first in line first.
        pids: The numbers its next players get.
        since: When the host listed it.
    """

    lobby: Lobby
    id: int
    host: TheaterConnection
    type: str
    name: str
    address: IPv4Address
    port: int
    internal_address: str | None
    internal_port: int | None
    ugid: str
    server: GameServer | None
    listed: bool
    key: str
    join: str
    max_players: int
    max_observers: int
    observers: int
    queue_length: int
    dequeue: bool
    password: bool
    version: str
    basic: dict[str, str]
    details: dict[str, str] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)
    entered: dict[int, Persona] = field(default_factory=dict)
    queue: list[Player] = field(default_factory=list)
    pids: Iterator[int] = field(default_factory=lambda: itertools.count(1))
    since: datetime = field(default_factory=lambda: datetime.now(UTC))

    def everyone(self) -> list[Player]:
        """Return its players and those waiting in its queue."""
        return [*self.players.values(), *self.queue]

    def in_game(self) -> list[Player]:
        """Return the participants its host reported in the game, which game lists show."""
        return [
            player
            for player in self.players.values()
            if player.participant and player.state is PlayerState.ENTERED
        ]

    def taken(self) -> int:
        """Return how many participants join or are in the game."""
        return sum(player.participant for player in self.players.values())

    def has_room(self, player: Player) -> bool:
        """Return whether a player fits besides the participants joining or in the game.

        Observers are left to the host, which knows how many watch.

        Args:
            player: The player that wants a place.
        """
        return not player.participant or self.taken() < self.max_players
