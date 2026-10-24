import struct
from dataclasses import dataclass
from enum import IntEnum

from openfesl.protocol.packet import Packet, PacketError

SUBPROTOCOL = "openfesl.v1"
MAX_HOST_LENGTH = 253

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


type Frame = Open | Data | Close


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
    if channel not in Channel:
        raise FrameError(f"unknown channel {channel}")
    if number == 0:
        raise FrameError("connection number 0 is never used")
    connection = ConnectionKey(Channel(channel), number)
    body = message[_HEADER.size :]
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


def encode_frame(frame: Data | Close) -> bytes:
    """Return the WebSocket message for a frame that the server sends.

    Args:
        frame: A packet for a game connection, or the request to close it.
    """
    match frame:
        case Data(connection, packet):
            return _encode_header(FrameType.DATA, connection) + packet.encode()
        case Close(connection):
            return _encode_header(FrameType.CLOSE, connection)


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
