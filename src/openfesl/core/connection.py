from ipaddress import IPv4Address, IPv6Address
from typing import Protocol

from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Open, PresenceUpdate

type Address = IPv4Address | IPv6Address


class Outbox(Protocol):
    """Where the answers and pushes of one game connection go."""

    def send(self, packet: Packet) -> None:
        """Queue a packet for the game connection.

        Args:
            packet: The answer or push to send.
        """

    async def send_paced(self, packet: Packet) -> None:
        """Queue one packet of a long burst, first waiting while the session's queue is half full.

        Args:
            packet: The packet to send.
        """

    def close(self) -> None:
        """Close the game connection once the packets queued before have gone out."""

    @property
    def busy(self) -> bool:
        """Whether half of the session's queue waits for the game, so that what can wait should."""


class PresenceOutbox(Protocol):
    """Where the presence of a session goes, for the chat client on the player's PC."""

    def send(self, update: PresenceUpdate) -> None:
        """Queue presence for the hook.

        Args:
            update: The chat application and what it shows.
        """


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

    def follow_presence(self, outbox: PresenceOutbox | None) -> None:
        """Send the player's presence whenever it changes, as the hook asked, or stop.

        Args:
            outbox: Where presence goes; none when the hook asks for none.
        """

    async def end(self) -> None:
        """Forget the session after all of its connections have closed."""
