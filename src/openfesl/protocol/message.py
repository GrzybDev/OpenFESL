import re
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from typing import Annotated, Any, Self

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel

from openfesl.protocol.fields import Fields, format_fields, parse_fields

_SEGMENT = re.compile(r"\{[^}]*\}|[^.]+")
_BAD_MAP_KEY = re.compile(r'[ \t\n}"=]')
_ASCII_UPPER = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def _date(text: str) -> date:
    month, day, year = text.split("-")
    return date(int(year), _MONTHS.index(month.upper()) + 1, int(day))


def _parse_date(value: object) -> object:
    return _date(value) if isinstance(value, str) else value


def _parse_date_time(value: object) -> object:
    if not isinstance(value, str):
        return value
    day, time = value.split()[:2]
    hour, minute, second = (int(part) for part in time.split(":"))
    return datetime.combine(_date(day), datetime_time(hour, minute, second), UTC)


def _storable(value: str) -> str:
    if "\0" in value:
        raise ValueError("text holds a NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        # The codec's own message quotes the character, which may be part of a password.
        raise ValueError("text is not UTF-8") from None
    return value


FeslDate = Annotated[date, BeforeValidator(_parse_date)]
# Text that is kept or compared, so it must be UTF-8 without NUL; other bytes fail validation.
Text = Annotated[str, AfterValidator(_storable)]
FeslDateTime = Annotated[datetime, BeforeValidator(_parse_date_time)]
# The SDK reads and writes its numbers as 32-bit integers and its ids as 64-bit ones.
Int32 = Annotated[int, Field(ge=-(2**31), lt=2**31)]
Int64 = Annotated[int, Field(ge=-(2**63), lt=2**63)]


class Message(BaseModel):
    """A FESL or Theater body as typed fields, named on the wire in camel case unless aliased.

    Text travels as UTF-8; bytes that are not UTF-8 survive a round trip as surrogate escapes.

    Dotted keys map to nested messages, `name.[]` counts and `name.N` entries to lists, and
    `name.{key}` entries to dicts. Booleans travel as 0 and 1, floats with four decimals, and
    FeslDate and FeslDateTime fields in the SDK's `OCT-24-2026` and
    `"OCT-24-2026 12:34:56 UTC"` forms.
    """

    # Validation errors reach the log, so they leave out the values, which may be secrets.
    model_config = ConfigDict(
        frozen=True, alias_generator=to_camel, validate_by_name=True, hide_input_in_errors=True
    )

    @classmethod
    def parse(cls, body: bytes) -> Self:
        """Read a message from a packet's payload.

        Args:
            body: The packet's payload.

        Raises:
            pydantic.ValidationError: When required fields are missing or do not fit their type.
        """
        return cls.model_validate(_tree(parse_fields(body)))

    def fields(self) -> Fields:
        """Return the message as flat key=value fields, leaving out fields set to None."""
        fields: Fields = []
        _flatten("", self, fields)
        return fields

    def format(self) -> bytes:
        """Return the message as a packet payload."""
        return format_fields(self.fields())


def _flatten(key: str, value: object, fields: Fields) -> None:
    match value:
        case None:
            pass
        case BaseModel():
            for name, info in type(value).model_fields.items():
                child = info.serialization_alias or info.alias or name
                _flatten(f"{key}.{child}" if key else child, getattr(value, name), fields)
        case bool():
            fields.append((key, "1" if value else "0"))
        case int():
            fields.append((key, str(int(value))))
        case float():
            fields.append((key, f"{value:.4f}"))
        case datetime():
            moment = value.astimezone(UTC)
            fields.append((key, f"{_month(moment)}-{moment:%d-%Y %H:%M:%S} UTC"))
        case date():
            fields.append((key, f"{_month(value)}-{value.day}-{value.year}"))
        case str():
            fields.append((key, _to_wire(value)))
        case dict():
            for map_key, item in value.items():
                if not map_key or _BAD_MAP_KEY.search(map_key):
                    raise ValueError(f"map key {map_key!r} cannot be written")
                _flatten(f"{key}.{{{_to_wire(map_key)}}}", item, fields)
        case list() | tuple():
            fields.append((f"{key}.[]", str(len(value))))
            for index, item in enumerate(value):
                _flatten(f"{key}.{index}", item, fields)
        case _:
            raise TypeError(f"{key} holds a {type(value).__name__}, which has no wire form")


def _to_wire(text: str) -> str:
    return text.encode("utf-8", "surrogateescape").decode("latin-1")


def _from_wire(text: str) -> str:
    return text.encode("latin-1").decode("utf-8", "surrogateescape")


def _month(day: date) -> str:
    return _MONTHS[day.month - 1]


def _tree(fields: Fields) -> Any:
    # As in the SDK, the first of several keys that differ only in case wins.
    root: dict[str, Any] = {}
    seen: set[str] = set()
    for key, value in fields:
        folded = key.translate(_ASCII_UPPER)
        if folded in seen:
            continue
        seen.add(folded)
        *parents, leaf = _SEGMENT.findall(key) or [key]
        node: Any = root
        for parent in parents:
            node = node.setdefault(parent, {})
            if not isinstance(node, dict):
                break
        else:
            if isinstance(node.get(leaf), dict):
                continue
            node[leaf] = _from_wire(value)
    return _collapse(root)


def _collapse(node: dict[str, Any]) -> Any:
    children = {
        key: _collapse(child) if isinstance(child, dict) else child for key, child in node.items()
    }
    if "[]" in children:
        count = children.pop("[]")
        length = int(count) if isinstance(count, str) and count.isdigit() else 0
        # Only the entries that arrived, so a huge count costs nothing.
        entries = [(int(key), child) for key, child in children.items() if key.isdigit()]
        entries.sort(key=lambda entry: entry[0])
        return [child for index, child in entries if index < length]
    children.pop("{}", None)
    if children and all(key.startswith("{") for key in children):
        return {_from_wire(key[1:-1]): child for key, child in children.items()}
    return children
