import logging
from collections.abc import Mapping
from typing import Any

from openfesl.core.connection import Address, Connection, Outbox
from openfesl.core.fesl import FeslConnection, Route
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Channel, Open

logger = logging.getLogger(__name__)


class GameSession:
    """One game process, as one WebSocket of the hook carries it, and its game connections."""

    def __init__(
        self, routes: Mapping[tuple[str, str], Route[Any]], address: Address | None
    ) -> None:
        """Start a session for a game process.

        Args:
            routes: The FESL handlers by service command and TXN.
            address: The public address of the player's PC, when known.
        """
        self.address = address
        self._routes = routes

    def open(self, opened: Open, outbox: Outbox) -> Connection:
        """Start handling a connection that the game opened; Theater is not served yet.

        Args:
            opened: The host the game looked up and the ports on both ends.
            outbox: Where the connection's packets go.
        """
        if opened.connection.channel is Channel.FESL:
            return FeslConnection(outbox, self._routes)
        logger.info("Refusing a %s connection to %s", opened.connection.channel.name, opened.host)
        outbox.close()
        return _Refused()

    async def end(self) -> None:
        """Forget the session after all of its connections have closed."""


class _Refused:
    async def receive(self, packet: Packet) -> None:
        pass

    async def close(self) -> None:
        pass
