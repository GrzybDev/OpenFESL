import asyncio
import logging
import secrets
from collections.abc import Callable
from ipaddress import IPv4Address
from typing import Any

from openfesl.core.accounts import AccountState
from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.hosting import NO_GAME, NOT_FOUND, HostedGame, Hosting, Player, PlayerState
from openfesl.core.personas import Persona
from openfesl.core.theater import (
    BAD_PARAMETERS,
    FAILED,
    TheaterConnection,
    TheaterError,
    TheaterRoute,
)
from openfesl.protocol.packet import PacketError
from openfesl.protocol.theater import (
    EnterGame,
    EnterGameHostRequest,
    EnterGameHostResponse,
    EnterGameNotice,
    GameRequest,
    PlayerEnteredAnswer,
    PlayerRequest,
    QueueEnter,
    QueueKick,
    QueuePosition,
    QueueUpdate,
    Refusal,
    theater_packet,
)

logger = logging.getLogger(__name__)

DENIED = "jden"
QUEUED = "queu"

CLOSED = "C"
PARTICIPANT = "P"
OBSERVER = "O"

# Reasons travel as letters the game turns into its own numbers.
CLOSED_REASON = "c"
FULL_REASON = "f"
TIMED_OUT_REASON = "t"
# The host's own reason for a player it does not let in; the game shows every reason but a
# full game alike.
NOT_ALLOWED_REASON = "4"

# How long a player waits for its host's decision before the server refuses it.
HOST_ANSWER_TIMEOUT = 10.0


class Joining:
    """Players joining hosted games: the host decides, the server queues, tickets and tells."""

    def __init__(self, hosting: Hosting) -> None:
        """Let players join the games of a server.

        Args:
            hosting: The games hosts list.
        """
        self._hosting = hosting
        self._watched: set[TheaterConnection] = set()
        hosting.on_change(self._admit)

    def follow(self, feed: AdminFeed) -> None:
        """Kick players from their games as the admin asks, and those the admin signs out.

        Args:
            feed: The admin's actions as they are recorded.
        """
        feed.on(AdminAction.PLAYER_KICK, self._kick_persona)
        feed.on(AdminAction.ACCOUNT_STATE, self._kick_account)

    def routes(self) -> dict[str, TheaterRoute[Any]]:
        """Return the handlers of the commands that join, queue and leave games."""
        return {
            "EGAM": TheaterRoute(EnterGame, self.enter),
            "EGRS": TheaterRoute(EnterGameHostResponse, self.decide),
            "PENT": TheaterRoute(PlayerRequest, self.entered),
            "PLVT": TheaterRoute(PlayerRequest, self.exited),
            "ECNL": TheaterRoute(GameRequest, self.leave),
            "DQEG": TheaterRoute(PlayerRequest, self.dequeue),
            "QKCK": TheaterRoute(QueueKick, self.kick_queued),
            "UQUE": TheaterRoute(QueueUpdate, self.reorder),
        }

    async def enter(self, connection: TheaterConnection, request: EnterGame, tid: int) -> None:
        """Answer EGAM: ask the game's host about the player, or queue it while the game is full.

        The answer comes at once; the player learns the host's decision from EGEG.

        Args:
            connection: The player's connection.
            request: The game and the player's addresses.
            tid: The request's TID.

        Raises:
            TheaterError: When the session has no public IPv4 address for the host to reach, the
                game does not exist, is closed, or is full with no place in its queue, or the
                question to the host would not fit in a packet.
        """
        address = connection.session.address
        if not isinstance(address, IPv4Address):
            logger.warning("Refusing EGAM: the player has no public IPv4 address")
            raise TheaterError(FAILED)
        assert connection.persona is not None
        hosted, looked_up = self._target(connection, request)
        player = Player(
            pid=next(hosted.pids),
            persona=connection.persona,
            connection=connection,
            participant=request.ptype != OBSERVER,
            address=address,
            port=request.port,
            internal_address=request.r_int_ip or address,
            internal_port=request.r_int_port or request.port,
            request=request.attributes,
            group=request.r_groupid,
            looked_up=looked_up,
            ticket=str(10**9 + secrets.randbelow(9 * 10**9)),
        )
        game = GameRequest(lid=hosted.lobby.id, gid=hosted.id)
        if connection is hosted.host:
            # A host that takes a seat in its own game enters it without asking itself.
            hosted.players[player.pid] = player
            connection.send("EGAM", game, tid)
            self._approve(hosted, player)
            return
        try:
            # Checked at once, since the host may hear only later, when the player's turn comes;
            # a queue entry for the host (QENT) carries less than the question.
            theater_packet("EGRQ", _host_request(hosted, player).fields())
        except PacketError as error:
            logger.warning("Refusing EGAM from %s: %s", connection.persona.name, error)
            raise TheaterError(BAD_PARAMETERS) from None
        self._forget(hosted, connection.persona)
        if hosted.join == CLOSED:
            raise TheaterError(DENIED, Refusal(reason=CLOSED_REASON))
        if hosted.queue or not hosted.has_room(player):
            if len(hosted.queue) >= hosted.queue_length:
                raise TheaterError(DENIED, Refusal(reason=FULL_REASON))
            self._watch(connection)
            hosted.queue.append(player)
            length = len(hosted.queue)
            position = QueuePosition(lid=game.lid, gid=game.gid, qpos=length - 1, qlen=length)
            connection.send("EGAM", position, tid, QUEUED)
            if not hosted.dequeue:
                hosted.host.send("QENT", _queue_entry(hosted, player))
            return
        self._watch(connection)
        connection.send("EGAM", game, tid)
        self._ask_host(hosted, player)

    async def decide(
        self, connection: TheaterConnection, request: EnterGameHostResponse, tid: int
    ) -> None:
        """Answer EGRS and tell the player the host's decision.

        Args:
            connection: The host's connection.
            request: The decision.
            tid: The request's TID.

        Raises:
            TheaterError: When the host was not asked about such a player, or no longer waits.
        """
        hosted = self._hosting.own(connection, request)
        player = hosted.players.get(request.pid)
        if player is None or player.state is not PlayerState.JOINING:
            raise TheaterError(NOT_FOUND)
        player.stop_waiting()
        # The answer starts the host's link to the player, so it goes out first.
        connection.send("EGRS", None, tid)
        if request.allowed:
            self._approve(hosted, player)
        else:
            self._refuse(hosted, player, request.reason)

    async def entered(
        self, connection: TheaterConnection, request: PlayerRequest, tid: int
    ) -> None:
        """Answer PENT: the player connected to the host and is in the game.

        Args:
            connection: The host's connection.
            request: The player.
            tid: The request's TID.
        """
        player = self._hosting.player(connection, request)
        player.stop_waiting()
        player.state = PlayerState.ENTERED
        connection.send("PENT", PlayerEnteredAnswer(pid=request.pid), tid)

    async def exited(self, connection: TheaterConnection, request: PlayerRequest, tid: int) -> None:
        """Answer PLVT: a player left the host's game, which frees its place.

        Args:
            connection: The host's connection.
            request: The player.
            tid: The request's TID.
        """
        hosted = self._hosting.own(connection, request)
        if (player := hosted.players.pop(request.pid, None)) is not None:
            player.stop_waiting()
        connection.send("PLVT", None, tid)
        self._admit(hosted)

    async def leave(self, connection: TheaterConnection, request: GameRequest, tid: int) -> None:
        """Answer ECNL: a player leaves a game it joins, plays or waits for.

        A host that knows the player is told to drop it (KICK).

        Args:
            connection: The player's connection.
            request: The game.
            tid: The request's TID.
        """
        connection.send("ECNL", None, tid)
        hosted = self._hosting.find(connection.game, request.lid, request.gid)
        if hosted is None:
            return
        for player in hosted.everyone():
            if player.connection is connection and player.connection is not hosted.host:
                self._withdraw(hosted, player)

    async def dequeue(
        self, connection: TheaterConnection, request: PlayerRequest, tid: int
    ) -> None:
        """Answer DQEG: a host lets a waiting player in, which the server then asks it about.

        Args:
            connection: The host's connection.
            request: The player.
            tid: The request's TID.
        """
        hosted = self._hosting.own(connection, request)
        player = self._queued(hosted, request.pid)
        hosted.queue.remove(player)
        connection.send("DQEG", None, tid)
        self._ask_host(hosted, player)
        self._report_queue(hosted)

    async def kick_queued(
        self, connection: TheaterConnection, request: QueueKick, tid: int
    ) -> None:
        """Answer QKCK: a host sends a waiting player away with its reason.

        Args:
            connection: The host's connection.
            request: The player and the reason.
            tid: The request's TID.
        """
        hosted = self._hosting.own(connection, request)
        player = self._queued(hosted, request.pid)
        hosted.queue.remove(player)
        connection.send("QKCK", None, tid)
        refusal = Refusal(lid=hosted.lobby.id, gid=hosted.id, reason=request.reason)
        player.connection.send("EGEG", refusal, error=DENIED)
        self._report_queue(hosted)

    async def reorder(self, connection: TheaterConnection, request: QueueUpdate, tid: int) -> None:
        """Answer UQUE: a host puts its queue in a new order; players it leaves out go last.

        Args:
            connection: The host's connection.
            request: The PIDs in their new order.
            tid: The request's TID.
        """
        hosted = self._hosting.own(connection, request)
        wanted = [int(pid) for pid in request.queue.split(",") if pid.strip().isdigit()]
        order = {pid: index for index, pid in enumerate(wanted)}
        hosted.queue.sort(key=lambda player: order.get(player.pid, len(order)))
        connection.send("UQUE", None, tid)
        self._report_queue(hosted)

    def _target(
        self, connection: TheaterConnection, request: EnterGame
    ) -> tuple[HostedGame, Persona | None]:
        if request.lid is not None and request.gid is not None:
            hosted = self._hosting.find(connection.game, request.lid, request.gid)
            if hosted is not None:
                return hosted, None
        elif request.user or request.uid is not None:
            found = self._hosting.find_by_player(
                connection.game, request.type, request.user, request.uid
            )
            if found is not None:
                return found
        raise TheaterError(NO_GAME)

    def _ask_host(self, hosted: HostedGame, player: Player) -> None:
        player.state = PlayerState.JOINING
        hosted.players[player.pid] = player
        hosted.host.send("EGRQ", _host_request(hosted, player))
        loop = asyncio.get_running_loop()
        player.deadline = loop.call_later(HOST_ANSWER_TIMEOUT, self._expire, hosted, player)

    def _approve(self, hosted: HostedGame, player: Player) -> None:
        player.state = PlayerState.APPROVED
        host = hosted.host.persona
        assert host is not None
        notice = EnterGameNotice(
            lid=hosted.lobby.id,
            gid=hosted.id,
            pid=player.pid,
            ticket=player.ticket,
            ugid=hosted.ugid,
            ekey=hosted.key,
            huid=host.id,
            i=str(hosted.address),
            p=hosted.port,
            pl=hosted.host.platform,
            # The player's game pairs both addresses, so a host without a LAN one gets its own.
            int_ip=hosted.internal_address or str(hosted.address),
            int_port=hosted.internal_port or hosted.port,
        )
        player.connection.send("EGEG", notice)
        logger.info("Player %d (%s) may join game %d", player.pid, player.persona.name, hosted.id)

    def _refuse(self, hosted: HostedGame, player: Player, reason: str | None) -> None:
        hosted.players.pop(player.pid, None)
        refusal = Refusal(lid=hosted.lobby.id, gid=hosted.id, reason=reason)
        player.connection.send("EGEG", refusal, error=DENIED)
        logger.info("Player %d (%s) refused by game %d", player.pid, player.persona.name, hosted.id)
        self._admit(hosted)

    def _expire(self, hosted: HostedGame, player: Player) -> None:
        if hosted.players.get(player.pid) is player and player.state is PlayerState.JOINING:
            player.deadline = None
            logger.info("Game %d did not answer about player %d in time", hosted.id, player.pid)
            self._refuse(hosted, player, TIMED_OUT_REASON)

    def _withdraw(self, hosted: HostedGame, player: Player) -> None:
        player.stop_waiting()
        if player in hosted.queue:
            hosted.queue.remove(player)
            if not hosted.dequeue:
                hosted.host.send("QLVT", _player_id(hosted, player))
            self._report_queue(hosted)
            return
        if hosted.players.pop(player.pid, None) is not None:
            hosted.host.send("KICK", _player_id(hosted, player))
            self._admit(hosted)

    def _kick_persona(self, action: LoggedAction) -> None:
        persona_id = int(action.target)
        self._kick(lambda persona: persona.id == persona_id)

    def _kick_account(self, action: LoggedAction) -> None:
        # The host's link to a player outlives the player's sign-out, so the host is told too.
        if AccountState(action.details["state"]) is not AccountState.ACTIVE:
            account_id = int(action.target)
            self._kick(lambda persona: persona.account_id == account_id)

    def _kick(self, kicked: Callable[[Persona], bool]) -> None:
        for hosted in self._hosting.every():
            for player in hosted.everyone():
                if not kicked(player.persona) or player.connection is hosted.host:
                    continue
                logger.info(
                    "Kicking player %d (%s) from game %d",
                    player.pid,
                    player.persona.name,
                    hosted.id,
                )
                waiting = player.state in {PlayerState.QUEUED, PlayerState.JOINING}
                self._withdraw(hosted, player)
                if waiting:
                    refusal = Refusal(lid=hosted.lobby.id, gid=hosted.id, reason=NOT_ALLOWED_REASON)
                    player.connection.send("EGEG", refusal, error=DENIED)

    def _forget(self, hosted: HostedGame, persona: Persona) -> None:
        # A player that joins again replaces what is left of its earlier attempt.
        for player in hosted.everyone():
            if player.persona.id == persona.id:
                self._withdraw(hosted, player)

    def _admit(self, hosted: HostedGame) -> None:
        if not hosted.dequeue or not hosted.queue:
            return
        while hosted.queue and hosted.has_room(hosted.queue[0]):
            self._ask_host(hosted, hosted.queue.pop(0))
        self._report_queue(hosted)

    def _report_queue(self, hosted: HostedGame) -> None:
        for position, player in enumerate(hosted.queue):
            update = QueuePosition(
                lid=hosted.lobby.id, gid=hosted.id, qpos=position, qlen=len(hosted.queue)
            )
            player.connection.send("QLEN", update)

    def _queued(self, hosted: HostedGame, pid: int) -> Player:
        for player in hosted.queue:
            if player.pid == pid:
                return player
        raise TheaterError(NOT_FOUND)

    def _watch(self, connection: TheaterConnection) -> None:
        if connection not in self._watched:
            self._watched.add(connection)
            connection.on_close(lambda: self._abandon(connection))

    def _abandon(self, connection: TheaterConnection) -> None:
        # The host drops the players it let in once their link to it breaks; the server only
        # drops those still waiting for a place or for the host's decision.
        self._watched.discard(connection)
        for hosted in list(self._hosting.all_games(connection.game)):
            for player in hosted.everyone():
                if player.connection is not connection:
                    continue
                if player.state is PlayerState.QUEUED:
                    self._withdraw(hosted, player)
                elif player.state is PlayerState.JOINING:
                    player.stop_waiting()
                    hosted.players.pop(player.pid, None)
                    self._admit(hosted)


def _host_request(hosted: HostedGame, player: Player) -> EnterGameHostRequest:
    looked_up = player.looked_up
    return EnterGameHostRequest(
        lid=hosted.lobby.id,
        gid=hosted.id,
        pid=player.pid,
        name=player.persona.name,
        uid=player.persona.id,
        ticket=player.ticket,
        ip=str(player.address),
        port=player.port,
        # The host only builds the player's address when the LAN one is there.
        r_int_ip=str(player.internal_address),
        r_int_port=player.internal_port,
        ptype=PARTICIPANT if player.participant else OBSERVER,
        r_user=looked_up.name if looked_up else None,
        r_uid=looked_up.id if looked_up else None,
        r_groupid=player.group,
        attributes=player.request,
    )


def _player_id(hosted: HostedGame, player: Player) -> PlayerRequest:
    return PlayerRequest(lid=hosted.lobby.id, gid=hosted.id, pid=player.pid)


def _queue_entry(hosted: HostedGame, player: Player) -> QueueEnter:
    return QueueEnter(
        lid=hosted.lobby.id,
        gid=hosted.id,
        pid=player.pid,
        uid=player.persona.id,
        name=player.persona.name,
        r_groupid=player.group,
        attributes=player.request,
    )
