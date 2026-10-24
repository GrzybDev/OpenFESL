from typing import Annotated, ClassVar, Self

from pydantic import AfterValidator, ConfigDict, Field

from openfesl.protocol.fields import Fields, format_fields, parse_fields
from openfesl.protocol.message import Message, _flatten, _from_wire, _to_wire
from openfesl.protocol.packet import HEADER_SIZE, Packet, PacketError

REQUEST = 0x40
ANSWER = 0x00

# The game reads each Theater packet whole into a buffer of 8224 bytes and drops the connection
# when a header announces more; a packet of exactly that size overruns it by its closing NUL.
MAXIMUM_PACKET_SIZE = 8223


def _wire_key(name: str) -> str:
    return name.upper().replace("_", "-")


def _cut(size: int) -> AfterValidator:
    # The game keeps no more than this many bytes of the field, so neither does the server.
    return AfterValidator(lambda text: _from_wire(_to_wire(text)[:size]))


VersionText = Annotated[str, _cut(63)]
ShortText = Annotated[str, _cut(31)]


class TheaterMessage(Message):
    """A Theater body: flat fields named in upper case with hyphens unless aliased.

    Keys that start with the class's attribute prefix, such as `B-U-` for a game's basic
    attributes, go to `attributes` under their full key and are written back the same way.
    As in the SDK, the first of several keys that differ only in case wins.
    """

    model_config = ConfigDict(frozen=True, alias_generator=_wire_key, validate_by_name=True)

    attribute_prefix: ClassVar[str] = ""

    @classmethod
    def parse(cls, body: bytes) -> Self:
        """Read a message from a packet's payload.

        Args:
            body: The packet's payload.

        Raises:
            pydantic.ValidationError: When required fields are missing or do not fit their type.
        """
        values: dict[str, object] = {}
        attributes: dict[str, str] = {}
        seen: set[str] = set()
        prefix = cls.attribute_prefix.upper()
        for key, value in parse_fields(body):
            folded = key.upper()
            if folded in seen:
                continue
            seen.add(folded)
            if prefix and folded.startswith(prefix):
                attributes[key] = _from_wire(value)
            else:
                values[key] = _from_wire(value)
        if prefix:
            values["attributes"] = attributes
        return cls.model_validate(values)

    def fields(self) -> Fields:
        """Return the message as flat fields, attribute maps under their own keys."""
        fields: Fields = []
        for name, info in type(self).model_fields.items():
            value = getattr(self, name)
            if isinstance(value, dict):
                fields.extend((key, _to_wire(item)) for key, item in value.items())
            else:
                _flatten(info.alias or name, value, fields)
        return fields


def theater_packet(command: str, fields: Fields, error: str | None = None) -> Packet:
    """Return a Theater packet that the server sends.

    Args:
        command: The packet's command, such as CONN.
        fields: Its body.
        error: The four-letter error code that fails the request, written in the header.

    Raises:
        PacketError: When the packet is larger than the game reads.
    """
    code = int.from_bytes(error.encode("ascii"), "big") if error else ANSWER
    payload = format_fields(fields)
    if (size := HEADER_SIZE + len(payload)) > MAXIMUM_PACKET_SIZE:
        raise PacketError(f"{size} bytes exceed the {MAXIMUM_PACKET_SIZE} the game reads")
    return Packet(command, code >> 24, code & 0xFFFFFF, payload)


def transaction_id(fields: Fields) -> int:
    """Return a Theater body's TID, which pairs a request with its answer; 0 when absent.

    Args:
        fields: The packet's fields.
    """
    for key, value in fields:
        if key.upper() == "TID":
            return int(value) if value.isdigit() else 0
    return 0


class Connect(TheaterMessage):
    """CONN: the game's first request, naming its product, version and platform."""

    prot: int = 0
    prod: str = ""
    vers: VersionText = ""
    plat: ShortText = ""


class ConnectAnswer(TheaterMessage):
    """The answer to CONN; the game closes a connection silent for `activityTimeoutSecs`."""

    time: int
    activity_timeout_secs: int = Field(alias="activityTimeoutSecs")
    prot: int


class Login(TheaterMessage):
    """USER: the session key that NuLoginPersona handed out for the persona."""

    lkey: str


class LoginAnswer(TheaterMessage):
    name: str


class Echo(TheaterMessage):
    """ECHO: a datagram that asks for the public address the game's socket is seen from."""

    type: int = 1
    uid: int = 0


class EchoAnswer(TheaterMessage):
    ip: str
    port: int
    type: int
    uid: int
