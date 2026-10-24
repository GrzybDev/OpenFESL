import base64

import pytest

from openfesl.protocol.fesl import (
    ANSWER,
    DEFAULT_FRAGMENT_SIZE,
    FRAGMENTED_ANSWER,
    FragmentError,
    Reassembler,
    fragment,
)
from openfesl.protocol.fields import format_fields, parse_fields
from openfesl.protocol.packet import Packet

GAME_BUFFER = DEFAULT_FRAGMENT_SIZE + 128


def test_a_large_answer_reaches_the_game_in_pieces_it_can_reassemble() -> None:
    payload = format_fields((f"stats.{index}.key", f"key{index:05}") for index in range(1500))
    answer = Packet("rank", ANSWER, 42, payload)

    pieces = fragment(answer, DEFAULT_FRAGMENT_SIZE)

    assert len(pieces) > 1
    data = []
    for piece in pieces:
        fields = dict(parse_fields(piece.payload))
        assert (piece.command, piece.kind, piece.number) == ("rank", FRAGMENTED_ANSWER, 42)
        assert len(piece.encode()) < GAME_BUFFER
        assert fields["decodedSize"] == str(len(payload))
        data.append(fields["data"])
    assert all(len(text) % 4 == 0 for text in data[:-1])
    assert b"".join(base64.b64decode(text) for text in data) == payload


def test_a_small_answer_stays_one_packet() -> None:
    answer = Packet("acct", ANSWER, 7, format_fields([("TXN", "NuLogin")]))

    assert fragment(answer, DEFAULT_FRAGMENT_SIZE) == [answer]


def test_a_request_the_game_sent_in_pieces_is_joined_again() -> None:
    body = format_fields([("TXN", "UpdateStats"), ("u.[]", "0")])
    encoded = base64.b64encode(body).decode("ascii")
    reassembler = Reassembler()
    pieces = [encoded[:8], encoded[8:]]

    first, last = (
        Packet("rank", 0xF0, 9, format_fields([("size", str(len(encoded))), ("data", piece)]))
        for piece in pieces
    )

    assert reassembler.add(first) is None
    assert reassembler.add(last) == Packet("rank", 0xD0, 9, body)


def test_pieces_that_overshoot_their_size_are_refused() -> None:
    piece = Packet("rank", 0xF0, 9, format_fields([("size", "4"), ("data", "QUJDRA==")]))

    with pytest.raises(FragmentError):
        Reassembler().add(piece)
