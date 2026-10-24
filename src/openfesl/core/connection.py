from ipaddress import IPv4Address, IPv6Address
from typing import Protocol

from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Open

type Address = IPv4Address | IPv6Address


class Outbox(Protocol):
    """Where the answers and pushes of one game connection go."""

    def send(self, packet: Packet) -> None:
        """Queue a packet for the game connection.

        Args:
            packet: The answer or push to send.
        """

    def close(self) -> None:
        """Close the game connection once the packets queued before have gone out."""

    @property
    def busy(self) -> bool:
        """Whether half of the session's queue waits for the game, so that what can wait should."""


class Connection(Protocol):
    """The core's side of one game connection."""

    async def receive(self, packet: Packet) -> None:
        """Handle a packet that the game sent.

        Args:
            packet: One whole FESL or Theater packet.
        """

    async def close(self) -> None:
        """Forget the connection's state; the game closed it or its session ended."""


class Session(Protocol):
    """The core's side of one game process and all of its connections."""

    def open(self, opened: Open, outbox: Outbox) -> Connection:
        """Start handling a connection that the game opened.

        Args:
            opened: The host the game looked up and the ports on both ends.
            outbox: Where the connection's packets go.
        """

    async def end(self) -> None:
        """Forget the session after all of its connections have closed."""
