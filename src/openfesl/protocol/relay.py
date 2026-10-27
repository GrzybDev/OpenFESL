import json
import struct
from dataclasses import dataclass
from enum import IntEnum

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from openfesl.protocol.packet import Packet, PacketError

SUBPROTOCOL = "openfesl.v1"
MAX_HOST_LENGTH = 253
# The channel of frames about the whole session rather than one game connection.
SESSION_CHANNEL = 0

_HEADER = struct.Struct(">BBH")
_OPEN = struct.Struct(">HHB")


class FrameError(ValueError):
    """A WebSocket message that breaks the relay protocol."""


class Channel(IntEnum):
    """The game service a relayed connection belongs to."""

    FESL = 1
    THEATER = 2
    THEATER_DATAGRAM = 3


class FrameType(IntEnum):
    OPEN = 1
    DATA = 2
    CLOSE = 3
    PRESENCE = 4


@dataclass(frozen=True, slots=True)
class ConnectionKey:
    """Names one game connection of a session; the hook picks the number per channel."""

    channel: Channel
    number: int


@dataclass(frozen=True, slots=True)
class Open:
    """The game opened a connection to the host it looked up, from its own socket's port."""

    connection: ConnectionKey
    host: str
    port: int
    game_port: int


@dataclass(frozen=True, slots=True)
class Data:
    """One whole game packet of a connection, in either direction."""

    connection: ConnectionKey
    packet: Packet


@dataclass(frozen=True, slots=True)
class Close:
    """The game closed the connection, or the server asks the hook to close it."""

    connection: ConnectionKey


@dataclass(frozen=True, slots=True)
class Presence:
    """What the player's chat client shows about the game, as UTF-8 JSON on the session channel.

    The hook asks for it in its first frame; the server answers whenever it changes.
    """

    body: bytes


type Frame = Open | Data | Close | Presence


class PresenceRequest(BaseModel):
    """The hook's presence frame: whether the player's chat client is to show presence."""

    enabled: bool = False


class Party(BaseModel):
    """How many players a match holds of how many it takes."""

    model_config = ConfigDict(frozen=True)

    size: int
    max: int


class Activity(BaseModel):
    """What the player's chat client shows about the game; the hook leaves out what is missing.

    Attributes:
        name: The game's name.
        details: The first line below the name.
        state: The second line.
        large_image: The game's image, a public image address or an asset key of the application.
        large_text: The large image's tooltip.
        small_image: The image in the large one's corner.
        small_text: The small image's tooltip.
        started_at: The start of the elapsed time, in milliseconds of Unix time.
        party: The players of the match.
        page: The path on the relay server's website that the activity's button opens.
    """

    model_config = ConfigDict(frozen=True, alias_generator=to_camel, validate_by_name=True)

    name: str | None = None
    details: str | None = None
    state: str | None = None
    large_image: str | None = None
    large_text: str | None = None
    small_image: str | None = None
    small_text: str | None = None
    started_at: int | None = None
    party: Party | None = None
    page: str | None = None


class PresenceUpdate(BaseModel):
    """The server's presence frame: the chat application, and the activity or none to clear it."""

    model_config = ConfigDict(frozen=True)

    application: str
    activity: Activity | None = None

    def encode(self) -> bytes:
        """Return the frame's body as JSON, without the activity's missing fields."""
        activity = (
            self.activity.model_dump(by_alias=True, exclude_none=True) if self.activity else None
        )
        return json.dumps(
            {"application": self.application, "activity": activity}, separators=(",", ":")
        ).encode("ascii")


def decode_frame(message: bytes) -> Frame:
    """Read a frame that the hook sent.

    Args:
        message: The binary WebSocket message.

    Raises:
        FrameError: When the message breaks the relay protocol, which ends the session.
    """
    if len(message) < _HEADER.size:
        raise FrameError(f"{len(message)} bytes are shorter than the frame header")
    frame_type, channel, number = _HEADER.unpack_from(message)
    body = message[_HEADER.size :]
    if frame_type == FrameType.PRESENCE or channel == SESSION_CHANNEL:
        return _decode_presence(frame_type, channel, number, body)
    if channel not in Channel:
        raise FrameError(f"unknown channel {channel}")
    if number == 0:
        raise FrameError("connection number 0 is never used")
    connection = ConnectionKey(Channel(channel), number)
    match frame_type:
        case FrameType.OPEN:
            return _decode_open(connection, body)
        case FrameType.DATA:
            try:
                return Data(connection, Packet.decode(body))
            except PacketError as error:
                raise FrameError(f"data is not one whole packet: {error}") from error
        case FrameType.CLOSE:
            if body:
                raise FrameError(f"close carries {len(body)} bytes")
            return Close(connection)
        case _:
            raise FrameError(f"unknown frame type {frame_type}")


def encode_frame(frame: Data | Close | Presence) -> bytes:
    """Return the WebSocket message for a frame that the server sends.

    Args:
        frame: A packet for a game connection, the request to close it, or presence.
    """
    match frame:
        case Data(connection, packet):
            return _encode_header(FrameType.DATA, connection) + packet.encode()
        case Close(connection):
            return _encode_header(FrameType.CLOSE, connection)
        case Presence(body):
            return _HEADER.pack(FrameType.PRESENCE, SESSION_CHANNEL, 0) + body


def _decode_presence(frame_type: int, channel: int, number: int, body: bytes) -> Presence:
    # Only presence travels on the session channel, and only there, never empty.
    if frame_type != FrameType.PRESENCE or channel != SESSION_CHANNEL or number != 0:
        raise FrameError(f"frame type {frame_type} on channel {channel}, connection {number}")
    if not body:
        raise FrameError("presence carries no body")
    return Presence(body)


def _decode_open(connection: ConnectionKey, body: bytes) -> Open:
    if len(body) < _OPEN.size:
        raise FrameError(f"open carries {len(body)} bytes, shorter than its fields")
    port, game_port, host_length = _OPEN.unpack_from(body)
    host = body[_OPEN.size :]
    if len(host) != host_length:
        raise FrameError(f"open names a {host_length}-byte host but carries {len(host)} bytes")
    if not 0 < host_length <= MAX_HOST_LENGTH or not host.isascii():
        raise FrameError(f"host {host!r} is not an ASCII name of 1 to {MAX_HOST_LENGTH} bytes")
    return Open(connection, host.decode("ascii"), port, game_port)


def _encode_header(frame_type: FrameType, connection: ConnectionKey) -> bytes:
    return _HEADER.pack(frame_type, connection.channel, connection.number)
