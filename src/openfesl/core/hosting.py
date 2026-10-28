import itertools
import logging
import secrets
from collections.abc import Callable, Iterator, Mapping
from ipaddress import IPv4Address
from typing import Any

from openfesl.core.games import Game, Lobby
from openfesl.core.hosted_games import HostedGame, Player
from openfesl.core.personas import Persona
from openfesl.core.servers import (
    BAD_SECRET,
    Admission,
    Approval,
    GameServer,
    new_identity,
    reported_provider,
)
from openfesl.core.theater import FAILED, TheaterConnection, TheaterError, TheaterRoute
from openfesl.protocol.theater import (
    CreateGame,
    CreateGameAnswer,
    GameRequest,
    PlayerRequest,
    RemoveGame,
    UpdateGame,
    UpdateGameDetails,
    UpdatePlayer,
    attributes_size,
)

logger = logging.getLogger(__name__)

GAME = "G"
PLAYGROUP = "P"

IN_GAME = "ingm"
LOBBY_FULL = "lbfl"
NO_GAME = "ngam"
NOT_FOUND = "ntfn"

# Each attribute map travels whole in one row of a game list, which the game drops past 8223
# bytes, so a map as written stays below that with room for the row's other fields, which
# the game's read limits keep under about 1200 bytes.
MAXIMUM_ATTRIBUTES_SIZE = 6144


class Hosting:
    """The games hosts list in the lobbies of every game; one process holds them all."""

    def __init__(self, approval: Approval) -> None:
        """Start with empty lobbies.

        Args:
            approval: Issues dedicated servers their identities and decides who sees their games.
        """
        self._approval = approval
        self._lobbies: dict[tuple[str, int], dict[int, HostedGame]] = {}
        self._ids = itertools.count(1)
        self._changed: list[Callable[[HostedGame], object]] = []
        self._ended: list[Callable[[HostedGame], object]] = []

    def routes(self) -> dict[str, TheaterRoute[Any]]:
        """Return the handlers of the commands that host games."""
        return {
            "CGAM": TheaterRoute(CreateGame, self.create),
            "UBRA": TheaterRoute(GameRequest, self.bracket),
            "UGAM": TheaterRoute(UpdateGame, self.update),
            "UGDE": TheaterRoute(UpdateGameDetails, self.update_details),
            "UPLA": TheaterRoute(UpdatePlayer, self.update_player),
            "RGAM": TheaterRoute(RemoveGame, self.remove),
        }

    def on_change(self, listener: Callable[[HostedGame], object]) -> None:
        """Call a function after a host changes how many players its game takes or who may join.

        Args:
            listener: Called with the game.
        """
        self._changed.append(listener)

    def on_end(self, listener: Callable[[HostedGame], object]) -> None:
        """Call a function after a game leaves its lobby, however it ended.

        Args:
            listener: Called with the game as it was last.
        """
        self._ended.append(listener)

    def games(self, game: Game, lobby: Lobby) -> list[HostedGame]:
        """Return the games players see in a lobby, without those waiting for approval.

        Args:
            game: The game the lobby belongs to.
            lobby: The lobby.
        """
        return [hosted for hosted in self._lobby(game, lobby).values() if hosted.listed]

    def all_games(self, game: Game) -> Iterator[HostedGame]:
        """Return the games players see in every lobby of a game.

        Args:
            game: The game.
        """
        for lobby in game.lobbies:
            yield from self.games(game, lobby)

    def find(self, game: Game, lid: int, gid: int) -> HostedGame | None:
        """Return a game by its lobby and game ids, if players see it.

        Args:
            game: The game it belongs to.
            lid: Its lobby's id.
            gid: Its id.
        """
        hosted = self._lobbies.get((game.name, lid), {}).get(gid)
        return hosted if hosted is not None and hosted.listed else None

    def find_by_player(
        self, game: Game, type: str, name: str | None, uid: int | None
    ) -> tuple[HostedGame, Persona] | None:
        """Return a game of a type that a player hosts or plays in, and that player.

        Dedicated servers share their persona, so it names none of their games.

        Args:
            game: The game it belongs to.
            type: G for a game, P for a playgroup.
            name: The player's persona name, matched regardless of case.
            uid: The player's persona id.
        """
        for hosted in self.all_games(game):
            if hosted.type != type:
                continue
            people = [player.persona for player in hosted.players.values()]
            account = hosted.host.session.account
            if hosted.host.persona and account and not account.server:
                people.append(hosted.host.persona)
            for persona in people:
                if persona.id == uid or (name and persona.name.casefold() == name.casefold()):
                    return hosted, persona
        return None

    def own(self, connection: TheaterConnection, request: GameRequest) -> HostedGame:
        """Return a game that a request names, as long as the connection hosts it.

        Args:
            connection: The host's connection.
            request: The request.

        Raises:
            TheaterError: When the connection hosts no such game.
        """
        hosted = self._lobbies.get((connection.game.name, request.lid), {}).get(request.gid)
        if hosted is None or hosted.host is not connection:
            raise TheaterError(NO_GAME)
        return hosted

    async def create(self, connection: TheaterConnection, request: CreateGame, tid: int) -> None:
        """List a host's game in a lobby, answering with the ids and keys the host keeps.

        A dedicated server hosts under the identity the master issued it, which it sends again
        in every later CGAM; players see its games once the admin approves it, or at once
        while hosting is open.

        Args:
            connection: The host's connection.
            request: The game.
            tid: The request's TID.

        Raises:
            TheaterError: When the session has no public IPv4 address for players to reach, the
                host lists a game of the type already (it then removes it and asks again), its
                secret is wrong or another instance hosts under its id, the admin revoked it,
                or no lobby has room.
        """
        address = connection.session.address
        if not isinstance(address, IPv4Address):
            logger.warning("Refusing CGAM %r: the host has no public IPv4 address", request.name)
            raise TheaterError(FAILED)
        if any(game.type == request.type for game in self._hosted_by(connection)):
            raise TheaterError(IN_GAME)
        # Only a player's own playgroup needs no approval.
        if request.type != PLAYGROUP or connection.server:
            admission = await self._approval.admit(request.ugid, request.secret, request.name)
            ugid = admission.identity.ugid
            if any(
                hosted.ugid == ugid and hosted.host.session is not connection.session
                for hosted in self.every()
            ):
                logger.warning("Refusing server %s: another instance hosts under its id", ugid)
                raise TheaterError(BAD_SECRET)
        else:
            admission = Admission(new_identity(), server=None, listed=True)
        lobby = self._free_lobby(connection.game, request.lid, request.type)
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
            ugid=admission.identity.ugid,
            server=admission.server,
            listed=admission.listed,
            key=secrets.token_urlsafe(12),
            join=request.join,
            max_players=request.max_players,
            max_observers=request.max_observers,
            observers=request.num_observers,
            queue_length=request.qlen,
            dequeue=not request.disable_auto_dequeue,
            password=request.password,
            version=request.version,
            basic={},
        )
        _merge(hosted.basic, request.attributes)
        _label(connection.game, hosted)
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
            secret=admission.identity.secret,
            join=hosted.join,
        )
        connection.send("CGAM", answer, tid)
        await self._note_provider(connection.game, hosted, request.attributes)

    async def bracket(self, connection: TheaterConnection, request: GameRequest, tid: int) -> None:
        """Answer the UBRA that opens or closes a host's batch of updates.

        The updates inside carry the opening UBRA's TID and get no answer of their own.

        Args:
            connection: The host's connection.
            request: The game the updates are about.
            tid: The request's TID.
        """
        self.own(connection, request)
        connection.send("UBRA", None, tid)

    async def update(self, connection: TheaterConnection, request: UpdateGame, tid: int) -> None:
        """Apply UGAM: a host's changes to its game's name, join mode, capacity or attributes.

        Args:
            connection: The host's connection.
            request: The changes.
            tid: The TID of the bracket it belongs to.
        """
        hosted = self.own(connection, request)
        room = (hosted.max_players, hosted.join)
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
        _label(connection.game, hosted)
        if (hosted.max_players, hosted.join) != room:
            for listener in self._changed:
                listener(hosted)
        await self._note_provider(connection.game, hosted, request.attributes)

    async def update_details(
        self, connection: TheaterConnection, request: UpdateGameDetails, tid: int
    ) -> None:
        """Apply UGDE: the detailed attributes of a host's game that changed.

        Args:
            connection: The host's connection.
            request: The changes.
            tid: The TID of the bracket it belongs to.
        """
        _merge(self.own(connection, request).details, request.attributes)

    async def update_player(
        self, connection: TheaterConnection, request: UpdatePlayer, tid: int
    ) -> None:
        """Apply UPLA: the attributes of a player in a host's game that changed.

        Args:
            connection: The host's connection.
            request: The player and the changes.
            tid: The TID of the bracket it belongs to.
        """
        _merge(self.player(connection, request).attributes, request.attributes)

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

    def count(self, game: Game, lobby: Lobby) -> int:
        """Return how many games players see in a lobby; playgroups do not count.

        Args:
            game: The game the lobby belongs to.
            lobby: The lobby.
        """
        return sum(hosted.type == GAME for hosted in self.games(game, lobby))

    def _free_lobby(self, game: Game, wanted: int, type: str) -> Lobby:
        lobbies = [lobby for lobby in game.lobbies if lobby.id == wanted] or game.lobbies
        for lobby in lobbies:
            # A player lists one playgroup at most, so only games fill a lobby, hidden ones too.
            taken = sum(hosted.type == GAME for hosted in self._lobby(game, lobby).values())
            if type == PLAYGROUP or taken < lobby.max_games:
                return lobby
        raise TheaterError(LOBBY_FULL)

    def player(self, connection: TheaterConnection, request: PlayerRequest) -> Player:
        """Return a player past the queue of a game the connection hosts.

        Args:
            connection: The host's connection.
            request: The request that names the game and the player.

        Raises:
            TheaterError: When the connection hosts no such game or the game no such player.
        """
        player = self.own(connection, request).players.get(request.pid)
        if player is None:
            raise TheaterError(NOT_FOUND)
        return player

    def _lobby(self, game: Game, lobby: Lobby) -> dict[int, HostedGame]:
        return self._lobbies.get((game.name, lobby.id), {})

    def every(self) -> list[HostedGame]:
        """Return the games of every lobby of every game, those players do not see included."""
        return [hosted for lobby in self._lobbies.values() for hosted in lobby.values()]

    def _hosted_by(self, connection: TheaterConnection) -> list[HostedGame]:
        return [
            hosted
            for lobby in connection.game.lobbies
            for hosted in self._lobby(connection.game, lobby).values()
            if _same_host(hosted.host, connection)
        ]

    async def _note_provider(self, game: Game, hosted: HostedGame, sent: Mapping[str, str]) -> None:
        provider_id = _provider_in(game, sent)
        if hosted.server is None or provider_id is None or provider_id == hosted.server.provider_id:
            return
        server = await self._approval.attach(hosted.ugid, provider_id)
        if server is None or server.revoked:
            self.end(hosted)
            return
        self.update_server(hosted, server)

    def update_server(self, hosted: HostedGame, server: GameServer) -> None:
        """Show a game as the admin left the dedicated server that hosts it.

        Args:
            hosted: The game.
            server: The server, as stored now.
        """
        hosted.server = server
        _label(hosted.host.game, hosted)

    def end(self, hosted: HostedGame) -> None:
        """Remove a dedicated server's game whose server or provider the admin revoked.

        Args:
            hosted: The game.
        """
        # A dedicated server that loses its connection drops its players and quits after ten
        # losses in ten minutes, so the host keeps it: GREM ends the game, and its next CGAM is
        # refused.
        logger.warning(
            "Removing game %d: the admin revoked %s or its provider", hosted.id, hosted.ugid
        )
        if self._remove(hosted):
            hosted.host.send("GREM", GameRequest(lid=hosted.lobby.id, gid=hosted.id))

    def _remove(self, hosted: HostedGame) -> bool:
        listed = self._lobbies.get((hosted.host.game.name, hosted.lobby.id), {})
        if listed.pop(hosted.id, None) is None:
            return False
        logger.info("Game %d (%r) removed", hosted.id, hosted.name)
        for listener in self._ended:
            listener(hosted)
        for player in hosted.everyone():
            player.stop_waiting()
            if player.connection is not hosted.host:
                player.connection.send("GREM", GameRequest(lid=hosted.lobby.id, gid=hosted.id))
        return True


def _same_host(host: TheaterConnection, connection: TheaterConnection) -> bool:
    # A player hosts as its persona wherever it logged in last; the dedicated servers of a game
    # share one persona, so each server process hosts on its own.
    if host.server or connection.server:
        return host.session is connection.session
    return host.persona == connection.persona


def _label(game: Game, hosted: HostedGame) -> None:
    # Only the master server says who provides a game and whether the game's own mark shows,
    # whatever the host sends and however it spells the keys: the name of the provider its server
    # reports, once the admin knows it, and 1 only for an official server.
    server = hosted.server
    provider = server.provider if server else None
    _set(hosted.basic, game.provider_attribute, provider.name if provider else None)
    _set(hosted.basic, game.official_attribute, "1" if server and server.official else "0")


def _set(attributes: dict[str, str], name: str | None, value: str | None) -> None:
    if name is None:
        return
    for key in [key for key in attributes if key.casefold() == name.casefold()]:
        del attributes[key]
    if value is not None:
        attributes[name] = value


def _provider_in(game: Game, sent: Mapping[str, str]) -> bytes | None:
    name = game.provider_attribute
    for key, value in sent.items():
        if name and key.casefold() == name.casefold():
            return reported_provider(value)
    return None


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
