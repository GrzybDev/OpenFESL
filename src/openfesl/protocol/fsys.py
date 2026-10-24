from pydantic import Field

from openfesl.protocol.fesl import DEFAULT_FRAGMENT_SIZE
from openfesl.protocol.message import FeslDateTime, Message


class Hello(Message):
    """The game's first request on a FESL connection, naming the game and its SDK."""

    client_string: str
    sku: str = ""
    locale: str = ""
    client_platform: str = ""
    client_version: str = ""
    sdk_version: str = Field("", alias="SDKVersion")
    protocol_version: str = ""
    fragment_size: int = Field(DEFAULT_FRAGMENT_SIZE, gt=32)
    client_type: str = ""


class DomainPartition(Message):
    domain: str
    sub_domain: str


class HelloAnswer(Message):
    """Where the game finds Theater, which partition it plays in and the server's clock.

    An `activityTimeoutSecs` of 0 gives the game's own 240 seconds; without the key it waits 200.
    """

    domain_partition: DomainPartition
    activity_timeout_secs: int
    cur_time: FeslDateTime
    theater_ip: str
    theater_port: int


class MemoryRegion(Message):
    addr: str
    len: int


class MemCheck(Message):
    """The memory check that the game must answer before it may send more than Hello."""

    memcheck: list[MemoryRegion] = Field(default_factory=list)
    type: int = 0
    salt: int


class Ping(Message):
    """Traffic that keeps the game's activity timeout from closing an idle connection."""


class Goodbye(Message):
    """The game leaving, with the SDK's name for the reason and a free-text message."""

    reason: str = ""
    message: str = ""


class GoodbyePush(Message):
    """The server sending the game away; reason 2 means the account logged in elsewhere."""

    reason: int


class GetPingSitesAnswer(Message):
    """The sites the server browser pings to find the closest region; without any it pings none."""

    min_ping_sites_to_ping: int = 0
    ping_site: list[Message] = Field(default_factory=list)
