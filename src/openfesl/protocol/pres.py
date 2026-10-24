from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from openfesl.protocol.message import Message, Text

# The SDK's names for online, away, extended away, do not disturb, in a game and offline.
type Show = Literal["chat", "away", "xa", "dnd", "game", "disc", "unknown"]
# The SDK keeps attribute names in 32 bytes and values in 128; a quote could not be written back.
AttributeName = Annotated[str, StringConstraints(max_length=32, pattern=r'^[^"]+$')]
AttributeValue = Annotated[Text, Field(max_length=128)]


class UserRequest(Message):
    user_id: int = 0


class PresenceRequests(Message):
    """The personas a PresenceSubscribe or PresenceUnsubscribe names."""

    requests: list[UserRequest] = Field(default_factory=list)


class PresenceUser(Message):
    id: int
    name: str | None = None


class PresenceOutcome(Message):
    owner: PresenceUser
    outcome: int


class PresenceAnswer(Message):
    """One outcome per persona; the game takes pushes only about those with outcome 0."""

    responses: list[PresenceOutcome]


class Status(Message):
    """A player's presence: the state, an optional text and flags such as joinable=true."""

    show: Show = "unknown"
    status: Annotated[Text, Field(max_length=128)] | None = None
    attributes: dict[AttributeName, AttributeValue] = Field(default_factory=dict, max_length=8)


class SetPresenceStatus(Message):
    status: Status = Field(default_factory=Status)


class AsyncPresenceStatusEvent(Message):
    """A subscribed persona's presence; `initial` marks the snapshot that follows a subscription."""

    owner: PresenceUser
    initial: bool
    status: Status
