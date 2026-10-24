import re
from typing import Annotated, Literal, Self

from pydantic import Field

from openfesl.protocol.fields import Fields, parse_fields
from openfesl.protocol.message import FeslDateTime, Message, Text

# The SDK keeps message types, attachment keys and attachment types in 64 bytes, NUL included.
Name = Annotated[Text, Field(min_length=1, max_length=63)]
# The SDK reads message numbers as 32-bit integers.
MessageId = Annotated[int, Field(ge=0, le=0x7FFFFFFF)]
type Delivery = Literal["standard", "direct"]
type Purge = Literal["all", "all-recipients", "on-logoff"]

MAXIMUM_ATTACHMENTS = 8
MAXIMUM_ATTACHMENT_SIZE = 4096

_ATTACHMENT_TYPE = re.compile(r"retrieveAttachmentTypes\.(\d+)", re.IGNORECASE)
_ATTACHMENT_TYPES = "retrieveAttachmentTypes.[]"


class Attachment(Message):
    """Data a message carries; types starting with text/ hold text, others base64."""

    key: Name
    type: Name
    data: Annotated[Text, Field(max_length=MAXIMUM_ATTACHMENT_SIZE)]


class SendMessage(Message):
    """A message to personas; `expires` is its lifetime in seconds."""

    message_type: Name
    to: list[int] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list, max_length=MAXIMUM_ATTACHMENTS)
    expires: int = 0
    delivery_type: Delivery = "standard"
    purge_strategy: Purge = "all"


class RecipientStatus(Message):
    user_id: int
    error: int


class SendMessageAnswer(Message):
    """The stored message's number, 0 for a direct one, and what became of each recipient."""

    message_id: int
    status: list[RecipientStatus]


class User(Message):
    id: int
    name: str


class MessageInfo(Message):
    """A message as GetMessages lists it and as AsyncMessageEvent pushes it.

    Without `timeSent` the game cannot read the message; `expiration` counts seconds from it.
    """

    message_id: int
    message_type: str
    time_sent: FeslDateTime
    expiration: int
    sender: User = Field(alias="from")
    to: list[User]
    attachments: list[Attachment]


class GetMessages(Message):
    """Which messages to list; empty lists ask for every type, number and attachment type."""

    message_types: list[Text] = Field(default_factory=list)
    message_ids: list[MessageId] = Field(default_factory=list)
    attachment_types: list[Text] = Field(default_factory=list)
    box: Literal["inbox", "outbox"] = "inbox"


class GetMessagesAnswer(Message):
    messages: list[MessageInfo]


class GetMessageAttachments(Message):
    message_id: MessageId = 0
    keys: list[Text] = Field(default_factory=list)


class GetMessageAttachmentsAnswer(Message):
    attachments: list[Attachment]


class MessageIds(Message):
    """The messages a DeleteMessages or PurgeMessages names."""

    message_ids: list[MessageId] = Field(default_factory=list)


class AsyncPurgedEvent(Message):
    """Messages that left the receiving persona's boxes."""

    message_ids: list[int]

    def fields(self) -> Fields:
        """Return the push's fields, counting the ids under the key the SDK reads."""
        # The SDK reads this count from `messageIds[]`, without the dot of every other list.
        return [
            ("messageIds[]", value) if key == "messageIds.[]" else (key, value)
            for key, value in super().fields()
        ]


class ModifySettings(Message):
    """Which messages the player hears of as they arrive; empty lists take every type."""

    retrieve_message_types: list[Text] = Field(default_factory=list)
    retrieve_attachment_types: list[Text] = Field(default_factory=list)
    notify_messages: bool = True

    @classmethod
    def parse(cls, body: bytes) -> Self:
        """Read the settings, counting the attachment types by their entries.

        The SDK writes the attachment types' count as the number of message types.

        Args:
            body: The packet's payload.

        Raises:
            pydantic.ValidationError: When a field does not fit its type.
        """
        fields = parse_fields(body)
        indexes = [int(match[1]) for key, _ in fields if (match := _ATTACHMENT_TYPE.fullmatch(key))]
        entries = [
            (key, value) for key, value in fields if key.lower() != _ATTACHMENT_TYPES.lower()
        ]
        return cls.from_fields([*entries, (_ATTACHMENT_TYPES, str(max(indexes, default=-1) + 1))])
