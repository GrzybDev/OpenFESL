import pytest

from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import (
    Channel,
    Close,
    ConnectionKey,
    Data,
    FrameError,
    Open,
    decode_frame,
    encode_frame,
)

THEATER_1 = ConnectionKey(Channel.THEATER, 1)


def test_the_relay_protocol_example_decodes_and_the_answer_encodes_byte_for_byte() -> None:
    # The exchange at the end of OpenFESLHook's docs/relay-protocol.md.
    open_frame = bytes.fromhex("01020001 47DBC350 0F") + b"pc.theater.test"
    request = bytes.fromhex("02020001 434F4E4E 40000000 0000001A") + b"TID=1\nPROT=2\n\0"
    answer = bytes.fromhex("02020001 434F4E4E 00000000 00000029")
    answer += b"TID=1\nactivityTimeoutSecs=0\n\0"

    assert decode_frame(open_frame) == Open(THEATER_1, "pc.theater.test", 18395, 50000)
    assert decode_frame(request) == Data(THEATER_1, Packet("CONN", 0x40, 0, b"TID=1\nPROT=2\n\0"))
    reply = Data(THEATER_1, Packet("CONN", 0x00, 0, b"TID=1\nactivityTimeoutSecs=0\n\0"))
    assert encode_frame(reply) == answer
    assert decode_frame(bytes.fromhex("03020001")) == Close(THEATER_1)
    assert encode_frame(Close(THEATER_1)) == bytes.fromhex("03020001")


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(bytes.fromhex("0201"), id="short header"),
        pytest.param(bytes.fromhex("04010001"), id="unknown type"),
        pytest.param(bytes.fromhex("03040001"), id="unknown channel"),
        pytest.param(bytes.fromhex("03010000"), id="connection 0"),
        pytest.param(bytes.fromhex("03010001 00"), id="close with a body"),
        pytest.param(bytes.fromhex("01010001 4A564A57 05") + b"host", id="open host too short"),
        pytest.param(bytes.fromhex("01010001 4A564A57 00"), id="open without a host"),
        pytest.param(
            bytes.fromhex("01010001 4A564A57 02") + "é".encode(), id="open host not ASCII"
        ),
        pytest.param(
            bytes.fromhex("01010001 4A564A57 FE") + b"a" * 254, id="open host longer than DNS"
        ),
        pytest.param(bytes.fromhex("02010001 66737973 C0000001 0000000D"), id="data cut short"),
        pytest.param(
            bytes.fromhex("02010001 66737973 C0000001 0000000C 00"), id="data with trailing bytes"
        ),
        pytest.param(
            bytes.fromhex("02010001 66737973 C0000001 00100001") + bytes(0x100001 - 12),
            id="data above 1 MiB",
        ),
    ],
)
def test_frames_that_break_the_protocol_are_refused(message: bytes) -> None:
    with pytest.raises(FrameError):
        decode_frame(message)


def test_a_fesl_packet_keeps_its_kind_and_transaction_number() -> None:
    packet = Packet("acct", 0xC0, 0x123456, b"TXN=NuLogin\n\0")
    encoded = packet.encode()

    assert encoded[4:8] == bytes.fromhex("C0123456")
    assert Packet.decode(encoded) == packet
