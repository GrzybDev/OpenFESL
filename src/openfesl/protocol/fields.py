import re
from collections.abc import Iterable

type Fields = list[tuple[str, str]]

_ESCAPED = re.compile(r'[\x00-\x1f"%=\x7f]')
_UNESCAPED = re.compile(r"%(%|[0-9A-Fa-f]{2})")
_KEY = re.compile(r"[^\x00-\x20=]+")


def parse_fields(body: bytes) -> Fields:
    """Read the key=value fields of a FESL or Theater body the way the game's SDK reads them.

    Text is kept byte for byte as latin-1, since the SDK never decodes a character set.
    Fields end at any byte up to space outside quotes, everything after a NUL is ignored, and
    tokens without '=' are skipped.

    Args:
        body: The packet's payload.

    Returns:
        The fields in the order they arrived, duplicates included.
    """
    text = body.split(b"\0", 1)[0].decode("latin-1")
    fields: Fields = []
    position, end = 0, len(text)
    while position < end:
        if text[position] <= " ":
            position += 1
            continue
        key_end = position
        while key_end < end and text[key_end] > " " and text[key_end] != "=":
            key_end += 1
        if key_end == end or text[key_end] != "=":
            position = key_end
            continue
        key = text[position:key_end]
        start = key_end + 1
        if start < end and text[start] == '"':
            stop = start + 1
            while stop < end and text[stop] != '"' and text[stop] >= " ":
                stop += 1
            value = text[start + 1 : stop]
            position = stop + 1 if stop < end and text[stop] == '"' else stop
        else:
            stop = start
            while stop < end and text[stop] > " ":
                stop += 1
            value = text[start:stop]
            position = stop
        fields.append((key, _UNESCAPED.sub(_unescape, value)))
    return fields


def format_fields(fields: Iterable[tuple[str, str]]) -> bytes:
    """Write fields the way the game's SDK writes them, ending the body with a NUL.

    Args:
        fields: Keys and values, values as latin-1 text.

    Raises:
        ValueError: When a key is empty or holds '=' or a byte up to space, which no reader
            could find again.
    """
    lines = []
    for key, value in fields:
        if not _KEY.fullmatch(key):
            raise ValueError(f"key {key!r} cannot be written")
        lines.append(f"{key}={_escape(value)}\n")
    return "".join(lines).encode("latin-1") + b"\0"


def _escape(value: str) -> str:
    escaped = _ESCAPED.sub(lambda match: f"%{ord(match[0]):02x}", value)
    return f'"{escaped}"' if " " in value or "," in value else escaped


def _unescape(match: re.Match[str]) -> str:
    code = match[1]
    return "%" if code == "%" else chr(int(code, 16))
