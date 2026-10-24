import pytest

from openfesl.protocol.packet import PacketError
from openfesl.protocol.theater import MAXIMUM_PACKET_SIZE, theater_packet

# The header, "N=", the line's end and the body's closing NUL.
FRAMING = 16


def test_a_packet_counts_against_the_games_buffer_as_written_with_its_escapes() -> None:
    largest = theater_packet("GDAT", [("N", "x" * (MAXIMUM_PACKET_SIZE - FRAMING))])
    assert len(largest.encode()) == MAXIMUM_PACKET_SIZE

    # Each % travels as three bytes.
    with pytest.raises(PacketError):
        theater_packet("GDAT", [("N", "%" * ((MAXIMUM_PACKET_SIZE - FRAMING) // 3 + 1))])
