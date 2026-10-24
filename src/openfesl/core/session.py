import logging

from openfesl.core.connection import Address, Connection, Outbox
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Open

logger = logging.getLogger(__name__)


class GameSession:
    """One game process, as one WebSocket of the hook carries it, and its game connections."""

    def __init__(self, address: Address | None) -> None:
        """Start a session for a game process.

        Args:
            address: The public address of the player's PC, when known.
        """
        self.address = address

    def open(self, opened: Open, outbox: Outbox) -> Connection:
        """Close a connection that the game opened, as no game service answers it.

        Args:
            opened: The host the game looked up and the ports on both ends.
            outbox: Where the connection's packets go.
        """
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
