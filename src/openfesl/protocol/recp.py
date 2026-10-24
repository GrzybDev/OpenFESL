import re
from typing import Annotated

from pydantic import AfterValidator, Field

from openfesl.protocol.message import FeslDateTime, Message, Text

# The SDK reads keys into 32 bytes and warns about value buffers under 255, NULs included.
_KEY_BYTES = 31
_VALUE_BYTES = 254
# Bytes that a map entry's name `values.{key}` cannot hold.
_UNMAPPABLE = re.compile(r'[\x00-\x20"=}]')


def _key(value: str) -> str:
    if not value or _UNMAPPABLE.search(value) or len(value.encode("utf-8")) > _KEY_BYTES:
        raise ValueError(f"record key {value!r} cannot be kept")
    return value


def _value(value: str) -> str:
    if len(value.encode("utf-8")) > _VALUE_BYTES:
        raise ValueError("record value is too long")
    return value


# Every record is read both as a list and as a map, so its keys must also name map entries.
RecordKey = Annotated[Text, AfterValidator(_key)]
RecordValue = Annotated[Text, AfterValidator(_value)]


class GetRecord(Message):
    """A persona's record by name, as GetRecord and GetRecordAsMap ask for it.

    The SDK leaves the owner out for the session's own persona and refuses any owner type but 1
    before sending.
    """

    owner: int = 0
    owner_type: int = 1
    record_name: Annotated[Text, Field(min_length=1, max_length=63)]


class RecordEntry(Message):
    key: RecordKey
    value: RecordValue


class WriteRecord(GetRecord):
    """Entries to set and keys to remove, as AddRecord and UpdateRecord send them; TTL is unused."""

    values: list[RecordEntry] = Field(default_factory=list)
    remove: list[RecordKey] = Field(default_factory=list)


class WriteRecordAsMap(GetRecord):
    """Entries as a `values.{key}` map, as AddRecordAsMap and UpdateRecordAsMap send them."""

    values: dict[RecordKey, RecordValue] = Field(default_factory=dict)
    remove: list[RecordKey] = Field(default_factory=list)


class GetRecordAnswer(Message):
    """A record's entries, which the SDK reads by index until a key is missing."""

    values: list[RecordEntry]


class GetRecordAsMapAnswer(Message):
    """A record's entries as a map, under a prefix the SDK matches case-sensitively.

    Attributes:
        last_modified: When the record last changed.
        state: Read by the SDK without a meaning it shows; the reference servers send 1.
        ttl: The record's time to live, 0 as records do not expire.
        values: The entries by key.
    """

    last_modified: FeslDateTime
    state: int = 1
    ttl: int = Field(0, alias="TTL")
    values: dict[str, str]
