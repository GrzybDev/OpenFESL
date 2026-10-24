import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from openfesl.core.connection import Connection, Outbox
from openfesl.core.fesl import describe
from openfesl.core.games import Game, Games
from openfesl.core.personas import Persona
from openfesl.protocol.fields import Fields, parse_fields
from openfesl.protocol.packet import Packet, PacketError
from openfesl.protocol.relay import Channel, Open
from openfesl.protocol.theater import (
    REQUEST,
    Connect,
    ConnectAnswer,
    Echo,
    EchoAnswer,
    Login,
    LoginAnswer,
    TheaterMessage,
    theater_packet,
    transaction_id,
)

if TYPE_CHECKING:
    from openfesl.core.session import GameSession

logger = logging.getLogger(__name__)

UNAUTHORIZED = "auth"
BAD_PARAMETERS = "bpar"
FAILED = "misc"

# The game closes a connection that stays silent for the timeout and answers every PING, so
# the server pings well within it and drops a connection that stops answering.
ACTIVITY_TIMEOUT = 240
PING_INTERVAL = 60.0


class TheaterError(Exception):
    """Ends a Theater request with an error code in the answer's header instead of a result."""

    def __init__(self, code: str) -> None:
        """Remember the code for the answer.

        Args:
            code: Four ASCII letters, such as `ngam` for a game that does not exist.
        """
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TheaterRoute[T: TheaterMessage]:
    """How the server reads one Theater command and what handles it.

    The handler gets the request's TID and sends the answer itself, since some commands are
    followed by rows that carry the same TID and others have no answer at all.
    """

    request: type[T]
    handle: Callable[[TheaterConnection, T, int], Awaitable[None]]
    login: bool = True


class Theater:
    """The Theater service of every game: its connections, datagrams and commands."""

    def __init__(self, games: Games, routes: Mapping[str, TheaterRoute[Any]]) -> None:
        """Serve Theater for the games of a server.

        Args:
            games: The games the server serves.
            routes: The handlers by command.
        """
        self._games = games
        self._routes = routes

    def open(self, session: GameSession, opened: Open, outbox: Outbox) -> Connection | None:
        """Start handling a Theater connection or datagram socket, or refuse an unknown host.

        Args:
            session: The game process it belongs to.
            opened: The host the game looked up, which starts with its client string.
            outbox: Where the connection's packets go.
        """
        found = self._games.find(opened.host.split(".", 1)[0])
        if found is None:
            return None
        if opened.connection.channel is Channel.THEATER_DATAGRAM:
            return TheaterDatagrams(session, outbox, opened.game_port)
        return TheaterConnection(session, outbox, self._routes, found[0])


class TheaterConnection:
    """One Theater connection of a game: its requests, answers and pushes.

    Answers carry the request's TID and pushes none, as the game takes a packet whose TID
    matches no request it waits for as a push. Errors travel as four letters in the header.
    """

    def __init__(
        self,
        session: GameSession,
        outbox: Outbox,
        routes: Mapping[str, TheaterRoute[Any]],
        game: Game,
    ) -> None:
        """Start a connection that serves nothing but CONN and USER until the game logs in.

        Args:
            session: The game process it belongs to.
            outbox: Where the connection's packets go.
            routes: The handlers by command.
            game: The game whose Theater host the game connected to.
        """
        self.session = session
        self.game = game
        self.persona: Persona | None = None
        # Whether the persona is the one that every dedicated server of the game shares.
        self.server = False
        self._outbox = outbox
        self._routes = routes
        self._heard = time.monotonic()
        self._closers: list[Callable[[], object]] = []
        self.on_close(asyncio.create_task(self._keep_alive()).cancel)

    async def receive(self, packet: Packet) -> None:
        """Handle a request, or a packet the game sends without waiting for an answer.

        Args:
            packet: One whole Theater packet.
        """
        self._heard = time.monotonic()
        fields = parse_fields(packet.payload)
        tid = transaction_id(fields)
        request = packet.kind == REQUEST
        logger.debug("<- %s %s", packet.command, describe(fields))
        route = self._routes.get(packet.command)
        if route is None:
            # The game's replies to pushes, such as PING, need no answer.
            if request:
                logger.warning("Unknown Theater command %s", packet.command)
                self.send(packet.command, None, tid, error=FAILED)
            return
        try:
            if route.login and self.persona is None:
                raise TheaterError(UNAUTHORIZED)
            message = route.request.parse(packet.payload)
            await route.handle(self, message, tid)
        except ValidationError as error:
            logger.warning("Invalid Theater %s: %s", packet.command, error)
            if request:
                self.send(packet.command, None, tid, error=BAD_PARAMETERS)
        except TheaterError as error:
            logger.info("Theater %s failed with %s", packet.command, error.code)
            if request:
                self.send(packet.command, None, tid, error=error.code)

    def send(
        self,
        command: str,
        message: TheaterMessage | None,
        tid: int | None = None,
        error: str | None = None,
    ) -> None:
        """Send the game an answer, a row that follows one, or a push.

        Args:
            command: The packet's command.
            message: Its fields, if any.
            tid: The TID of the request it belongs to; none for a push.
            error: The error code that fails the request.
        """
        fields = message.fields() if message else []
        if tid is not None:
            fields.append(("TID", str(tid)))
        logger.debug("-> %s %s %s", command, error or "", describe(fields))
        if packet := _packet(command, fields, error):
            self._outbox.send(packet)

    def close_connection(self) -> None:
        """Close the connection once the packets queued before have gone out."""
        self._outbox.close()

    def on_close(self, closer: Callable[[], object]) -> None:
        """Run a function when the connection closes, such as removing the games it hosts.

        Args:
            closer: What to run.
        """
        self._closers.append(closer)

    async def close(self) -> None:
        """Stop everything the connection started."""
        for closer in self._closers:
            closer()

    async def _keep_alive(self) -> None:
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if time.monotonic() - self._heard > ACTIVITY_TIMEOUT:
                logger.info("Closing a Theater connection silent for %d s", ACTIVITY_TIMEOUT)
                self.close_connection()
                return
            self.send("PING", None)


class TheaterDatagrams:
    """One game socket that sends Theater datagrams; ECHO tells it the address it is seen from."""

    def __init__(self, session: GameSession, outbox: Outbox, port: int) -> None:
        """Answer the datagrams of a game socket.

        Args:
            session: The game process it belongs to, which knows its public address.
            outbox: Where the answers go.
            port: The port of the game's socket.
        """
        self._session = session
        self._outbox = outbox
        self._port = port

    async def receive(self, packet: Packet) -> None:
        """Answer an ECHO with the session's public IPv4 address and the socket's port.

        Without an answer the game uses its own listen port, so other datagrams and sessions
        without an IPv4 address go unanswered.

        Args:
            packet: One datagram.
        """
        if packet.command != "ECHO":
            logger.debug("Dropping a Theater %s datagram", packet.command)
            return
        address = self._session.address
        if not isinstance(address, IPv4Address):
            logger.warning("Not answering ECHO: the session has no public IPv4 address")
            return
        try:
            echo = Echo.parse(packet.payload)
        except ValidationError as error:
            logger.warning("Invalid Theater ECHO: %s", error)
            return
        answer = EchoAnswer(ip=str(address), port=self._port, type=echo.type, uid=echo.uid)
        fields = [*answer.fields(), ("TID", str(transaction_id(parse_fields(packet.payload))))]
        if answered := _packet("ECHO", fields):
            self._outbox.send(answered)

    async def close(self) -> None:
        """Nothing to forget; a datagram socket ends with its session."""


def _packet(command: str, fields: Fields, error: str | None = None) -> Packet | None:
    try:
        return theater_packet(command, fields, error)
    except PacketError as refusal:
        # A packet the game cannot read would cost the game its whole connection.
        logger.error("Not sending Theater %s: %s", command, refusal)
        return None


def theater_routes() -> dict[str, TheaterRoute[Any]]:
    """Return the handlers that connect a game to Theater and log its persona in."""

    async def connect(connection: TheaterConnection, request: Connect, tid: int) -> None:
        logger.info("Theater CONN from %s %s (%s)", request.prod, request.vers, request.plat)
        answer = ConnectAnswer(
            time=int(time.time()), activity_timeout_secs=ACTIVITY_TIMEOUT, prot=request.prot
        )
        connection.send("CONN", answer, tid)

    async def log_in(connection: TheaterConnection, request: Login, tid: int) -> None:
        session = connection.session
        persona = session.persona
        # The key NuLoginPersona handed out on this session, for this game's personas.
        if (
            persona is None
            or request.lkey != session.persona_key
            or persona.namespace != connection.game.namespace
        ):
            raise TheaterError(UNAUTHORIZED)
        connection.persona = persona
        connection.server = session.account is not None and session.account.server
        logger.info("Persona %d (%s) logged in to Theater", persona.id, persona.name)
        connection.send("USER", LoginAnswer(name=persona.name), tid)

    return {
        "CONN": TheaterRoute(Connect, connect, login=False),
        "USER": TheaterRoute(Login, log_in, login=False),
    }
