import itertools
import logging
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from ipaddress import IPv4Address
from typing import Any

from openfesl.core.games import Game, Lobby
from openfesl.core.personas import Persona
from openfesl.core.theater import FAILED, TheaterConnection, TheaterError, TheaterRoute
from openfesl.protocol.theater import (
    CreateGame,
    CreateGameAnswer,
    GameRequest,
    LobbyData,
    LobbyListAnswer,
    PlayerEnteredAnswer,
    PlayerRequest,
    RemoveGame,
    TheaterMessage,
    UpdateGame,
    UpdateGameDetails,
    UpdatePlayer,
    attributes_size,
)

logger = logging.getLogger(__name__)

IN_GAME = "ingm"
LOBBY_FULL = "lbfl"
NO_GAME = "ngam"
NOT_FOUND = "ntfn"

# Each attribute map travels whole in one row of a game list, which the game drops past 8223
# bytes, so a map as written stays below that with room for the row's other fields, which
# the game's read limits keep under about 1200 bytes.
MAXIMUM_ATTRIBUTES_SIZE = 6144


@dataclass(eq=False)
class Player:
    """A player in a hosted game, joining until its host reports it entered.

    Attributes:
        pid: The number the server gave the player within the game.
        persona: Who plays.
        entered: Whether the host reported the player in the game (PENT).
        attributes: The player's `P-` attributes by full key.
    """

    pid: int
    persona: Persona
    entered: bool = False
    attributes: dict[str, str] = field(default_factory=dict)


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
        secret: The secret its host proves the id with.
        key: The key the game's traffic is encrypted with, which joining players get too.
        join: Who may join: O open, W wait, C closed.
        max_players: How many participants it takes.
        max_observers: How many observers it takes.
        observers: How many observers it has.
        queue_length: How many players may wait for a place.
        password: Whether joining needs a password.
        version: The game protocol version its players need.
        basic: The `B-U-` attributes by full key.
        details: The `D-` attributes by full key.
        players: Its players by PID.
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
    secret: str
    key: str
    join: str
    max_players: int
    max_observers: int
    observers: int
    queue_length: int
    password: bool
    version: str
    basic: dict[str, str]
    details: dict[str, str] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)


class Hosting:
    """The games hosts list in the lobbies of every game; one process holds them all."""

    def __init__(self) -> None:
        """Start with empty lobbies."""
        self._lobbies: dict[tuple[str, int], dict[int, HostedGame]] = {}
        self._ids = itertools.count(1)

    def routes(self) -> dict[str, TheaterRoute[Any]]:
        """Return the handlers of the commands that list lobbies and host games."""
        return {
            "LLST": TheaterRoute(TheaterMessage, self.list_lobbies),
            "CGAM": TheaterRoute(CreateGame, self.create),
            "UBRA": TheaterRoute(GameRequest, self.bracket),
            "UGAM": TheaterRoute(UpdateGame, self.update),
            "UGDE": TheaterRoute(UpdateGameDetails, self.update_details),
            "UPLA": TheaterRoute(UpdatePlayer, self.update_player),
            "PENT": TheaterRoute(PlayerRequest, self.player_entered),
            "PLVT": TheaterRoute(PlayerRequest, self.player_exited),
            "RGAM": TheaterRoute(RemoveGame, self.remove),
        }

    def games(self, game: Game, lobby: Lobby) -> list[HostedGame]:
        """Return the games listed in a lobby.

        Args:
            game: The game the lobby belongs to.
            lobby: The lobby.
        """
        return list(self._lobbies.get((game.name, lobby.id), {}).values())

    async def list_lobbies(
        self, connection: TheaterConnection, request: TheaterMessage, tid: int
    ) -> None:
        """Answer LLST with the game's lobbies, one LDAT row each after the answer.

        Args:
            connection: The connection the request came on.
            request: The filters, which the server does not apply.
            tid: The request's TID, which the rows carry too.
        """
        lobbies = connection.game.lobbies
        connection.send("LLST", LobbyListAnswer(num_lobbies=len(lobbies)), tid)
        for lobby in lobbies:
            count = len(self.games(connection.game, lobby))
            row = LobbyData(
                lid=lobby.id,
                passing=count,
                name=lobby.name,
                locale=lobby.locale,
                max_games=lobby.max_games,
                favorite_games=0,
                favorite_players=0,
                num_games=count,
            )
            connection.send("LDAT", row, tid)

    async def create(self, connection: TheaterConnection, request: CreateGame, tid: int) -> None:
        """List a host's game in a lobby, answering with the ids and keys the host keeps.

        Args:
            connection: The host's connection.
            request: The game.
            tid: The request's TID.

        Raises:
            TheaterError: When the session has no public IPv4 address for players to reach, the
                host lists a game of the type already (it then removes it and asks again), or no
                lobby has room.
        """
        address = connection.session.address
        if not isinstance(address, IPv4Address):
            logger.warning("Refusing CGAM %r: the host has no public IPv4 address", request.name)
            raise TheaterError(FAILED)
        if any(game.type == request.type for game in self._hosted_by(connection)):
            raise TheaterError(IN_GAME)
        lobby = self._free_lobby(connection.game, request.lid)
        hosted = HostedGame(
            lobby=lobby,
            id=next(self._ids),
            host=connection,
            type=request.type,
            name=request.name,
            address=address,
            port=request.port,
            internal_address=request.int_ip,
            internal_port=request.int_port,
            ugid=str(uuid.uuid4()),
            secret=secrets.token_urlsafe(32),
            key=secrets.token_urlsafe(12),
            join=request.join,
            max_players=request.max_players,
            max_observers=request.max_observers,
            observers=request.num_observers,
            queue_length=request.qlen,
            password=request.password,
            version=request.version,
            basic={},
        )
        _merge(hosted.basic, request.attributes)
        self._lobbies.setdefault((connection.game.name, lobby.id), {})[hosted.id] = hosted
        connection.on_close(lambda: self._remove(hosted))
        logger.info(
            "Game %d (%r) listed in lobby %d from %s", hosted.id, hosted.name, lobby.id, address
        )
        answer = CreateGameAnswer(
            lid=lobby.id,
            gid=hosted.id,
            max_players=hosted.max_players,
            ekey=hosted.key,
            ugid=hosted.ugid,
            secret=hosted.secret,
            join=hosted.join,
        )
        connection.send("CGAM", answer, tid)

    async def bracket(self, connection: TheaterConnection, request: GameRequest, tid: int) -> None:
        """Answer the UBRA that opens or closes a host's batch of updates.

        The updates inside carry the opening UBRA's TID and get no answer of their own.

        Args:
            connection: The host's connection.
            request: The game the updates are about.
            tid: The request's TID.
        """
        self._own(connection, request)
        connection.send("UBRA", None, tid)

    async def update(self, connection: TheaterConnection, request: UpdateGame, tid: int) -> None:
        """Apply UGAM: a host's changes to its game's name, join mode, capacity or attributes.

        Args:
            connection: The host's connection.
            request: The changes.
            tid: The TID of the bracket it belongs to.
        """
        hosted = self._own(connection, request)
        if request.name is not None:
            hosted.name = request.name
        if request.join is not None:
            hosted.join = request.join
        if request.max_players is not None:
            hosted.max_players = request.max_players
        if request.max_observers is not None:
            hosted.max_observers = request.max_observers
        if request.num_observers is not None:
            hosted.observers = request.num_observers
        _merge(hosted.basic, request.attributes)

    async def update_details(
        self, connection: TheaterConnection, request: UpdateGameDetails, tid: int
    ) -> None:
        """Apply UGDE: the detailed attributes of a host's game that changed.

        Args:
            connection: The host's connection.
            request: The changes.
            tid: The TID of the bracket it belongs to.
        """
        _merge(self._own(connection, request).details, request.attributes)

    async def update_player(
        self, connection: TheaterConnection, request: UpdatePlayer, tid: int
    ) -> None:
        """Apply UPLA: the attributes of a player in a host's game that changed.

        Args:
            connection: The host's connection.
            request: The player and the changes.
            tid: The TID of the bracket it belongs to.
        """
        _merge(self._player(connection, request).attributes, request.attributes)

    async def player_entered(
        self, connection: TheaterConnection, request: PlayerRequest, tid: int
    ) -> None:
        """Answer PENT: the host let a joining player into its game.

        Args:
            connection: The host's connection.
            request: The player.
            tid: The request's TID.
        """
        self._player(connection, request).entered = True
        connection.send("PENT", PlayerEnteredAnswer(pid=request.pid), tid)

    async def player_exited(
        self, connection: TheaterConnection, request: PlayerRequest, tid: int
    ) -> None:
        """Answer PLVT: a player left the host's game.

        Args:
            connection: The host's connection.
            request: The player.
            tid: The request's TID.
        """
        self._own(connection, request).players.pop(request.pid, None)
        connection.send("PLVT", None, tid)

    async def remove(self, connection: TheaterConnection, request: RemoveGame, tid: int) -> None:
        """Answer RGAM: remove a host's game by its ids, or each of its games of a type.

        Args:
            connection: The host's connection.
            request: The game, or the type.
            tid: The request's TID.
        """
        for hosted in self._hosted_by(connection):
            if (request.lid, request.gid) == (hosted.lobby.id, hosted.id) or (
                request.gid is None and request.type == hosted.type
            ):
                self._remove(hosted)
        connection.send("RGAM", None, tid)

    def _free_lobby(self, game: Game, wanted: int) -> Lobby:
        lobbies = [lobby for lobby in game.lobbies if lobby.id == wanted] or game.lobbies
        for lobby in lobbies:
            if len(self.games(game, lobby)) < lobby.max_games:
                return lobby
        raise TheaterError(LOBBY_FULL)

    def _hosted_by(self, connection: TheaterConnection) -> Iterator[HostedGame]:
        for lobby in connection.game.lobbies:
            for hosted in self.games(connection.game, lobby):
                if hosted.host is connection:
                    yield hosted

    def _own(self, connection: TheaterConnection, request: GameRequest) -> HostedGame:
        hosted = self._lobbies.get((connection.game.name, request.lid), {}).get(request.gid)
        if hosted is None or hosted.host is not connection:
            raise TheaterError(NO_GAME)
        return hosted

    def _player(self, connection: TheaterConnection, request: PlayerRequest) -> Player:
        player = self._own(connection, request).players.get(request.pid)
        if player is None:
            raise TheaterError(NOT_FOUND)
        return player

    def _remove(self, hosted: HostedGame) -> None:
        listed = self._lobbies.get((hosted.host.game.name, hosted.lobby.id), {})
        if listed.pop(hosted.id, None) is not None:
            logger.info("Game %d (%r) removed", hosted.id, hosted.name)


def _merge(attributes: dict[str, str], changes: dict[str, str]) -> None:
    # Keys match regardless of case, as the game's lookups do, and keep their first spelling.
    # An empty value clears the key. A change that would outgrow a game list row is dropped.
    merged = dict(attributes)
    spelled = {key.casefold(): key for key in merged}
    for key, value in changes.items():
        known = spelled.setdefault(key.casefold(), key)
        if value:
            merged[known] = value
        else:
            merged.pop(known, None)
            del spelled[key.casefold()]
    if attributes_size(merged) > MAXIMUM_ATTRIBUTES_SIZE:
        logger.warning("Dropping attribute changes that make the map too large: %s", list(changes))
        return
    attributes.clear()
    attributes.update(merged)
