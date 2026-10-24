import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from openfesl.core.connection import Outbox
from openfesl.core.games import Client, Game
from openfesl.protocol.fesl import (
    ANSWER,
    DEFAULT_FRAGMENT_SIZE,
    FRAGMENT,
    ErrorAnswer,
    FieldError,
    FragmentError,
    Reassembler,
    fragment,
)
from openfesl.protocol.fields import Fields, format_fields, parse_fields
from openfesl.protocol.fsys import GoodbyePush, Hello
from openfesl.protocol.message import Message
from openfesl.protocol.packet import Packet

if TYPE_CHECKING:
    from openfesl.core.session import GameSession

logger = logging.getLogger(__name__)

PARAMETERS_ERROR = 21
HELLO_MISSING = 22
SYSTEM_ERROR = 99
SERVICE_FAILED = 193

_SECRET = re.compile(r"password|lkey|encrypted|ticket", re.IGNORECASE)


class TransactionError(Exception):
    """Ends a FESL transaction with an error code instead of a result."""

    def __init__(self, code: int, fields: Sequence[FieldError] = ()) -> None:
        """Remember the code for the answer.

        Args:
            code: The `errorCode` the game receives; any nonzero value fails the transaction.
            fields: The fields that were refused, which the game shows next to its form.
        """
        super().__init__(code)
        self.code = code
        self.fields = list(fields)


@dataclass(frozen=True, slots=True)
class Route[T: Message]:
    """How the server reads one FESL transaction and what handles it."""

    request: type[T]
    handle: Callable[[FeslConnection, T], Awaitable[Message | None]]


class FeslConnection:
    """One FESL connection of a game: its transactions, answers and pushes.

    Answers carry the request's number, pushes number 0, as the game matches answers by number
    alone. Requests the game sent in fragments are joined, and answers larger than the game's
    fragment size leave in fragments.
    """

    def __init__(
        self, session: GameSession, outbox: Outbox, routes: Mapping[tuple[str, str], Route[Any]]
    ) -> None:
        """Start a connection that knows nothing about the game until its Hello.

        Args:
            session: The game process the connection belongs to, which holds its login.
            outbox: Where the connection's packets go.
            routes: The handlers by service command and TXN.
        """
        self.session = session
        self.fragment_size = DEFAULT_FRAGMENT_SIZE
        self.hello: Hello | None = None
        self.game: Game | None = None
        self.client: Client | None = None
        self._outbox = outbox
        self._routes = routes
        self._reassembler = Reassembler()
        self._closers: list[Callable[[], object]] = []

    async def receive(self, packet: Packet) -> None:
        """Handle a request, or the game's answer to a push.

        Args:
            packet: One whole packet from the game, possibly a fragment.
        """
        if packet.kind & FRAGMENT:
            try:
                joined = self._reassembler.add(packet)
            except FragmentError as error:
                logger.warning("Closing a FESL connection: %s", error)
                self._outbox.close()
                return
            if joined is None:
                return
            packet = joined
        fields = parse_fields(packet.payload)
        txn = next((value for key, value in fields if key.upper() == "TXN"), "")
        logger.debug("<- %s %s #%d %s", packet.command, txn, packet.number, _describe(fields))
        route = self._routes.get((packet.command, txn))
        if route is None:
            logger.warning("Unknown FESL transaction %s %s", packet.command, txn)
            self._error(packet, txn, SYSTEM_ERROR)
            return
        if self.hello is None and txn != "Hello":
            self._error(packet, txn, HELLO_MISSING)
            return
        try:
            answer = await route.handle(self, route.request.parse(packet.payload))
        except ValidationError as error:
            logger.warning("Invalid %s %s: %s", packet.command, txn, error)
            self._error(packet, txn, PARAMETERS_ERROR)
        except TransactionError as error:
            self._error(packet, txn, error.code, error.fields)
        except Exception:
            # A failure no handler foresees, such as an unreachable database, fails only this
            # transaction. The SDK reads 99 as the service being unavailable; the game's login
            # dialog reads it as a key server timeout, while its account and persona dialogs
            # have a text for the account service's 193.
            logger.exception("Failed %s %s", packet.command, txn)
            self._error(packet, txn, SERVICE_FAILED if packet.command == "acct" else SYSTEM_ERROR)
        else:
            if answer is not None:
                self._send(packet.command, packet.number, txn, answer.fields())

    def greet(self, hello: Hello, game: Game, client: Client) -> None:
        """Remember who the game is once its Hello was accepted.

        Args:
            hello: The game's Hello.
            game: The game its client string names.
            client: The client string's settings.
        """
        self.hello = hello
        self.game = game
        self.client = client
        self.fragment_size = hello.fragment_size

    def disconnect(self, reason: int) -> None:
        """Send the game away with a Goodbye and close the connection.

        Args:
            reason: The Goodbye reason, such as 2 for a login elsewhere.
        """
        self.push("fsys", "Goodbye", GoodbyePush(reason=reason))
        self._outbox.close()

    def push(self, command: str, txn: str, message: Message) -> None:
        """Send the game a transaction it did not ask for.

        Args:
            command: The service, such as fsys.
            txn: The transaction's name.
            message: Its fields.
        """
        self._send(command, 0, txn, message.fields())

    def on_close(self, closer: Callable[[], object]) -> None:
        """Run a function when the connection closes, such as stopping its timers.

        Args:
            closer: What to run.
        """
        self._closers.append(closer)

    async def close(self) -> None:
        """Stop everything the connection started."""
        for closer in self._closers:
            closer()

    def _error(
        self, request: Packet, txn: str, code: int, fields: Sequence[FieldError] = ()
    ) -> None:
        answer = ErrorAnswer(error_code=code, error_container=list(fields) or None)
        self._send(request.command, request.number, txn, answer.fields())

    def _send(self, command: str, number: int, txn: str, fields: Fields) -> None:
        fields = [("TXN", txn), *fields]
        logger.debug("-> %s %s #%d %s", command, txn, number, _describe(fields))
        packet = Packet(command, ANSWER, number, format_fields(fields))
        for piece in fragment(packet, self.fragment_size):
            self._outbox.send(piece)


def _describe(fields: Fields) -> str:
    return " ".join(
        f"{key}={'***' if _SECRET.search(key) else value!r}"
        for key, value in fields
        if key != "TXN"
    )
