import base64
import binascii

from pydantic import Field, ValidationError

from openfesl.protocol.message import Message
from openfesl.protocol.packet import MAX_SIZE, Packet

ANSWER = 0x80
REQUEST = 0xC0
FRAGMENTED_ANSWER = 0xB0
FRAGMENT = 0x20

DEFAULT_FRAGMENT_SIZE = 8096


class FragmentError(ValueError):
    """Pieces of a fragmented request that do not add up to one packet."""


class _RequestPiece(Message):
    size: int = Field(gt=0, le=MAX_SIZE)
    data: str


class _AnswerPiece(Message):
    decoded_size: int
    size: int
    data: str


def fragment(packet: Packet, fragment_size: int) -> list[Packet]:
    """Split an answer or push the game could not take in one packet into base64 pieces.

    The game reassembles by `decodedSize` and decodes each piece on its own, so every piece but
    the last holds a multiple of four characters; its packet stays below the game's buffer of
    `fragment_size` plus 128 bytes.

    Args:
        packet: The answer or push.
        fragment_size: The largest payload the game takes whole, from its Hello.
    """
    if len(packet.payload) <= fragment_size:
        return [packet]
    encoded = base64.b64encode(packet.payload).decode("ascii")
    length = max(fragment_size - 32, 4) // 4 * 4
    return [
        Packet(
            packet.command,
            FRAGMENTED_ANSWER,
            packet.number,
            _AnswerPiece(
                decoded_size=len(packet.payload),
                size=len(encoded),
                data=encoded[start : start + length],
            ).format(),
        )
        for start in range(0, len(encoded), length)
    ]


class Reassembler:
    """Joins the pieces of requests that the game sent in fragments on one connection."""

    def __init__(self) -> None:
        """Start without pending pieces."""
        self._pieces: dict[int, list[str]] = {}
        self._pending = 0

    def add(self, packet: Packet) -> Packet | None:
        """Collect a piece of a fragmented request.

        Args:
            packet: A packet whose kind has the fragment bit.

        Returns:
            The whole request once its last piece arrived, otherwise None.

        Raises:
            FragmentError: When a piece is malformed, the pieces overshoot their size or the
                pending pieces of the connection exceed the largest packet.
        """
        try:
            piece = _RequestPiece.parse(packet.payload)
        except ValidationError as error:
            raise FragmentError(f"piece of request {packet.number} is malformed") from error
        pieces = self._pieces.setdefault(packet.number, [])
        pieces.append(piece.data)
        self._pending += len(piece.data)
        if self._pending > MAX_SIZE:
            raise FragmentError(f"{self._pending} characters of fragments are pending")
        received = sum(len(data) for data in pieces)
        if received < piece.size:
            return None
        del self._pieces[packet.number]
        self._pending -= received
        if received > piece.size:
            raise FragmentError(f"request {packet.number} overshoots {piece.size} characters")
        try:
            body = base64.b64decode("".join(pieces))
        except binascii.Error as error:
            raise FragmentError(f"request {packet.number} is not base64") from error
        return Packet(packet.command, packet.kind & ~FRAGMENT, packet.number, body)
