import struct
from dataclasses import dataclass
from typing import Self

HEADER_SIZE = 12
MAX_SIZE = 1 << 20

_HEADER = struct.Struct(">4sII")


class PacketError(ValueError):
    """Bytes that are not one whole FESL or Theater packet."""


@dataclass(frozen=True, slots=True)
class Packet:
    """A FESL or Theater packet: its command, the kind and number from its header, and its payload.

    FESL carries the transaction number in the 24 bits below the kind; Theater mostly leaves them 0.
    """

    command: str
    kind: int
    number: int
    payload: bytes

    def __post_init__(self) -> None:
        """Refuse values that do not fit the header.

        Raises:
            PacketError: When the command is not four ASCII characters, the kind or number is out
                of range, or the packet would be larger than the hook accepts.
        """
        if len(self.command) != 4 or not self.command.isascii():
            raise PacketError(f"command {self.command!r} is not four ASCII characters")
        if not 0 <= self.kind <= 0xFF:
            raise PacketError(f"kind {self.kind:#x} does not fit in a byte")
        if not 0 <= self.number <= 0xFFFFFF:
            raise PacketError(f"number {self.number:#x} does not fit in 24 bits")
        if HEADER_SIZE + len(self.payload) > MAX_SIZE:
            raise PacketError(f"{HEADER_SIZE + len(self.payload)} bytes exceed {MAX_SIZE}")

    @classmethod
    def decode(cls, data: bytes) -> Self:
        """Read one whole packet.

        Args:
            data: The packet with its header, with nothing before or after it.

        Raises:
            PacketError: When the data is not exactly one packet of a size the hook allows.
        """
        if len(data) < HEADER_SIZE:
            raise PacketError(f"{len(data)} bytes are shorter than the header")
        command, header_type, length = _HEADER.unpack_from(data)
        if length != len(data):
            raise PacketError(f"the header says {length} bytes, {len(data)} arrived")
        if not command.isascii():
            raise PacketError(f"command {command!r} is not ASCII")
        return cls(
            command.decode("ascii"), header_type >> 24, header_type & 0xFFFFFF, data[HEADER_SIZE:]
        )

    def encode(self) -> bytes:
        """Return the packet with its header."""
        header_type = self.kind << 24 | self.number
        length = HEADER_SIZE + len(self.payload)
        return _HEADER.pack(self.command.encode("ascii"), header_type, length) + self.payload
