from collections.abc import Mapping
from ipaddress import IPv4Address
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AfterValidator, ConfigDict, Field

from openfesl.protocol.fields import Fields, format_fields, parse_fields
from openfesl.protocol.message import Int32, Message, Text, _flatten, _from_wire, _to_wire
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
NameText = Annotated[str, _cut(127)]
ReasonText = Annotated[str, _cut(255)]
# The game reads ports as 16-bit numbers.
Port = Annotated[int, Field(ge=0, le=0xFFFF)]
GameType = Literal["G", "P"]
JoinMode = Literal["O", "W", "C"]


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


def attributes_size(attributes: Mapping[str, str]) -> int:
    """Return the size of the Theater body that attributes make, escapes included.

    Args:
        attributes: The attributes by full key.
    """
    return len(format_fields((key, _to_wire(value)) for key, value in attributes.items()))


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


class GameRequest(TheaterMessage):
    """A message about one game, named by its lobby and game ids, such as UBRA or GREM."""

    lid: int
    gid: int


class PlayerRequest(GameRequest):
    """A message about one player of a game, such as PENT, KICK or QLVT."""

    pid: int


class GameFilter(TheaterMessage):
    """LLST: which games a player's lists count, and the favourites it marks.

    `FILTER-ATTR-U-<key>` asks for games whose `B-U-<key>` holds that value. Favourites are
    `;`-separated persona names and ids, and game names and UGIDs.
    """

    attribute_prefix = "FILTER-ATTR-"

    filter_fav_only: bool = False
    filter_not_full: bool = False
    filter_not_private: bool = False
    filter_not_closed: bool = False
    filter_min_size: int = 0
    fav_player: Text = ""
    fav_game: Text = ""
    fav_player_uid: str = ""
    fav_game_uid: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)


class GameList(GameFilter):
    """GLST: the games of a lobby that pass the filters, from GID on and at most COUNT of them."""

    lid: int
    type: str | None = None
    gid: int = 0
    count: int = 0


class LobbyListAnswer(TheaterMessage):
    """The answer to LLST: how many LDAT rows follow it."""

    num_lobbies: int


class LobbyData(TheaterMessage):
    """LDAT: one lobby, sent after the LLST answer with its TID."""

    lid: int
    passing: int
    name: str
    locale: str
    max_games: int
    favorite_games: int
    favorite_players: int
    num_games: int


class GameListAnswer(TheaterMessage):
    """The answer to GLST: how many GDAT rows follow it, and how full the lobby is."""

    num_games: int
    lid: int
    lobby_max_games: int
    lobby_num_games: int


class GameData(TheaterMessage):
    """GDAT: one game as lists show it, a row after GLST or the answer to a lookup.

    AP counts the players in it, which a lookup's PDAT rows match.
    """

    attribute_prefix = "B-U-"

    lid: int
    gid: int
    n: str
    type: str
    i: str
    p: int
    pw: bool
    mp: int
    ap: int
    jp: int
    qp: int
    hn: str
    hu: int
    v: str
    pl: str
    j: str
    f: bool
    nf: int
    max_observers: int = Field(alias="B-maxObservers")
    num_observers: int = Field(alias="B-numObservers")
    version: str = Field(alias="B-version")
    attributes: dict[str, str] = Field(default_factory=dict)


class PlayerCount(TheaterMessage):
    """PCNT: how many players are in the games of a lobby, or of every lobby for LID -1."""

    lid: int = -1


class PlayerCountAnswer(TheaterMessage):
    count: int
    lid: int


class GameLookup(TheaterMessage):
    """GDAT: a player asks about one game, by its ids or by a player in it."""

    lid: int | None = None
    gid: int | None = None
    user: Text | None = None
    uid: int | None = None
    type: str = "G"


class GameDetails(GameRequest):
    """GDET: a game's detailed attributes, after the answer to a lookup."""

    attribute_prefix = "D-"

    ugid: str
    attributes: dict[str, str] = Field(default_factory=dict)


class PlayerData(PlayerRequest):
    """PDAT: one player in a game, after its GDET."""

    attribute_prefix = "P-"

    name: str
    uid: int
    attributes: dict[str, str] = Field(default_factory=dict)


class CreateGame(TheaterMessage):
    """CGAM: a host lists a game in a lobby, LID -1 leaving the choice to the server."""

    attribute_prefix = "B-U-"

    lid: int = -1
    name: NameText
    port: Port
    type: GameType = "G"
    qlen: Int32 = 0
    int_ip: ShortText | None = None
    int_port: Port | None = None
    max_players: Int32
    max_observers: Int32 = Field(0, alias="B-maxObservers")
    num_observers: Int32 = Field(0, alias="B-numObservers")
    disable_auto_dequeue: bool = False
    password: bool = False
    ugid: str = ""
    secret: str = ""
    version: VersionText = Field("", alias="B-version")
    join: JoinMode = "O"
    attributes: dict[str, str] = Field(default_factory=dict)


class CreateGameAnswer(TheaterMessage):
    """The answer to CGAM: the ids every later packet about the game names, and its keys."""

    lid: int
    gid: int
    max_players: int
    ekey: str
    ugid: str
    secret: str
    join: str


class UpdateGame(GameRequest):
    """UGAM: what changed in a hosted game, its basic attributes as deltas; never answered."""

    attribute_prefix = "B-U-"

    name: NameText | None = None
    join: JoinMode | None = None
    max_players: Int32 | None = None
    max_observers: Int32 | None = Field(None, alias="B-maxObservers")
    num_observers: Int32 | None = Field(None, alias="B-numObservers")
    attributes: dict[str, str] = Field(default_factory=dict)


class UpdateGameDetails(GameRequest):
    """UGDE: the detailed attributes of a hosted game that changed; never answered."""

    attribute_prefix = "D-"

    attributes: dict[str, str] = Field(default_factory=dict)


class UpdatePlayer(PlayerRequest):
    """UPLA: the attributes of a joined player that changed; never answered."""

    attribute_prefix = "P-"

    attributes: dict[str, str] = Field(default_factory=dict)


class PlayerEnteredAnswer(TheaterMessage):
    """The answer to PENT, which the host matches to the player by its PID."""

    pid: int


class RemoveGame(TheaterMessage):
    """RGAM: a host removes a game by its ids, or every game of a type it may still have."""

    lid: int | None = None
    gid: int | None = None
    type: str | None = None


class EnterGame(TheaterMessage):
    """EGAM: a player asks to join a game, named by its ids or by a player in it.

    The ports and LAN address are the player's own, which its host connects back to.
    """

    attribute_prefix = "R-U-"

    lid: int | None = None
    gid: int | None = None
    user: Text | None = None
    uid: int | None = None
    type: str = "G"
    port: int
    r_int_ip: IPv4Address | None = None
    r_int_port: int | None = None
    ptype: str = "P"
    r_groupid: int | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class QueuePosition(GameRequest):
    """Where a waiting player is in a full game's queue: the EGAM answer `queu`, and QLEN."""

    qpos: int
    qlen: int


class Refusal(TheaterMessage):
    """Why a join failed, as a letter such as `f` for full or the host's number."""

    lid: int | None = None
    gid: int | None = None
    reason: str | None = None


class EnterGameHostRequest(PlayerRequest):
    """EGRQ: asks a host whether a player may join, with the ticket the player proves it with."""

    attribute_prefix = "R-U-"

    name: str
    uid: int
    ticket: str
    ip: str
    port: int
    r_int_ip: str
    r_int_port: int
    ptype: str
    r_user: str | None = None
    r_uid: int | None = None
    r_groupid: int | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class EnterGameHostResponse(PlayerRequest):
    """EGRS: the host's decision, with its reason when it refuses."""

    allowed: bool
    reason: ReasonText | None = None


class EnterGameNotice(PlayerRequest):
    """EGEG: tells an allowed player where its host is and how to prove it may join."""

    ticket: str
    ugid: str
    ekey: str
    huid: int
    i: str
    p: int
    pl: str
    int_ip: str
    int_port: int


class QueueEnter(PlayerRequest):
    """QENT: tells a host that lets players in itself who waits in its queue."""

    attribute_prefix = "R-U-"

    uid: int
    name: str
    r_groupid: int | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class QueueKick(PlayerRequest):
    """QKCK: a host sends a waiting player away, with its reason."""

    reason: ReasonText | None = None


class QueueUpdate(GameRequest):
    """UQUE: a host's new order of its queue, as comma-separated PIDs."""

    queue: str = ""
