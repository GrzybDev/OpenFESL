from datetime import UTC, date, datetime

from hypothesis import given
from hypothesis import strategies as st
from pydantic import Field

from openfesl.protocol.fields import format_fields, parse_fields
from openfesl.protocol.message import FeslDate, FeslDateTime, Message

KEY_CHARACTERS = st.characters(min_codepoint=0x21, max_codepoint=0xFF, exclude_characters="=")
KEYS = st.text(KEY_CHARACTERS, min_size=1)
VALUES = st.text(st.characters(max_codepoint=0xFF))


@given(st.lists(st.tuples(KEYS, VALUES)))
def test_any_fields_survive_a_round_trip(fields: list[tuple[str, str]]) -> None:
    assert parse_fields(format_fields(fields)) == fields


def test_a_body_in_the_sdks_own_form_reads_as_written_and_writes_back_byte_for_byte() -> None:
    body = (
        b"TXN=GetStats\n"
        b'curTime="OCT-24-2026 09:05:07 UTC"\n'
        b'message="hi, there%22%25%3d"\n'
        b"stats.0.key=kills\n"
        b"stats.0.value=12.5000\n"
        b"stats.[]=1\n"
        b"props.{pref-level.name}=levels/mp_005\n"
        b"\x00"
    )

    fields = parse_fields(body)

    assert fields == [
        ("TXN", "GetStats"),
        ("curTime", "OCT-24-2026 09:05:07 UTC"),
        ("message", 'hi, there"%='),
        ("stats.0.key", "kills"),
        ("stats.0.value", "12.5000"),
        ("stats.[]", "1"),
        ("props.{pref-level.name}", "levels/mp_005"),
    ]
    assert format_fields(fields) == body


def test_fields_end_where_the_sdk_ends_them() -> None:
    body = b'a=1 b=%41%%x c="two words\nd=4\x00e=ignored'

    assert parse_fields(body) == [("a", "1"), ("b", "A%x"), ("c", "two words"), ("d", "4")]


class Partition(Message):
    domain: str
    sub_domain: str


class Stat(Message):
    key: str
    value: float


class Answer(Message):
    txn: str = Field(alias="TXN")
    ready: bool
    count: int
    since: FeslDate
    current_time: FeslDateTime = Field(alias="curTime")
    partition: Partition = Field(alias="domainPartition")
    stats: list[Stat]
    names: list[str]
    props: dict[str, str]
    comment: str | None = None


def test_a_message_writes_nested_lists_and_maps_and_reads_them_back() -> None:
    answer = Answer(
        txn="Hello",
        ready=True,
        count=-3,
        since=date(2026, 3, 9),
        current_time=datetime(2026, 10, 24, 9, 5, 7, tzinfo=UTC),
        partition=Partition(domain="eagames", sub_domain="BFBC2"),
        stats=[Stat(key="kills", value=12.5), Stat(key="deaths", value=3)],
        names=["Ann", "Łucja Nowak"],
        props={"pref-level": "mp_005"},
    )

    body = answer.format()

    assert body == (
        b"TXN=Hello\n"
        b"ready=1\n"
        b"count=-3\n"
        b"since=MAR-9-2026\n"
        b'curTime="OCT-24-2026 09:05:07 UTC"\n'
        b"domainPartition.domain=eagames\n"
        b"domainPartition.subDomain=BFBC2\n"
        b"stats.[]=2\n"
        b"stats.0.key=kills\n"
        b"stats.0.value=12.5000\n"
        b"stats.1.key=deaths\n"
        b"stats.1.value=3.0000\n"
        b"names.[]=2\n"
        b"names.0=Ann\n"
        b'names.1="\xc5\x81ucja Nowak"\n'
        b"props.{pref-level}=mp_005\n"
        b"\x00"
    )
    assert Answer.parse(body) == answer


class Request(Message):
    txn: str = Field(alias="TXN")
    names: list[str]
    games: dict[str, list[int]]


def test_a_message_reads_lists_and_maps_the_way_the_sdk_writes_them() -> None:
    # Counts after the entries, an entry past the count, a huge count and a key repeated in
    # another case, of which the SDK reads the first.
    body = (
        b"TXN=Lookup\ntxn=ignored\n"
        b"names.1=Bob\nnames.0=Ann\nnames.2=Eve\nnames.[]=2\n"
        b"games.{mp.005}.0=7\ngames.{mp.005}.[]=999999999\n\x00"
    )

    assert Request.parse(body) == Request(txn="Lookup", names=["Ann", "Bob"], games={"mp.005": [7]})


class Notes(Message):
    notes: dict[str, str]


def test_text_and_map_keys_travel_as_utf8_and_other_bytes_survive() -> None:
    body = "notes.{Łódź}=zażółć\n".encode() + b"notes.{raw}=\xff\xfe\n\x00"

    notes = Notes.parse(body)

    assert notes.notes["Łódź"] == "zażółć"
    assert notes.format() == body
