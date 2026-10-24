import asyncio
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from openfesl.core.associations import AssociationStore, blocking
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.personas import UNKNOWN_PERSONA, Persona, PersonaStore
from openfesl.core.session import GameSession, Players, playing_player
from openfesl.protocol.message import Message
from openfesl.protocol.xmsg import (
    AsyncPurgedEvent,
    Attachment,
    GetMessageAttachments,
    GetMessageAttachmentsAnswer,
    GetMessages,
    GetMessagesAnswer,
    MessageIds,
    MessageInfo,
    ModifySettings,
    Purge,
    RecipientStatus,
    SendMessage,
    SendMessageAnswer,
    User,
)

logger = logging.getLogger(__name__)

UNKNOWN_MESSAGE = 104

MAXIMUM_RECIPIENTS = 100
MAXIMUM_IDS = 500
# The most copies an inbox and an outbox hold each.
BOX_SIZE = 100
# The SDK's longest expiration, which it also sends for any it does not know.
MAXIMUM_LIFETIME = 30 * 24 * 3600
# A session sends a burst of this many messages and one more each interval after it, so that
# nobody floods another player's game.
SEND_BURST = 20
SEND_INTERVAL = 3.0

# The message ids that left each persona's boxes, by persona.
type Gone = dict[int, list[int]]


@dataclass(frozen=True, slots=True)
class Draft:
    """A message to store.

    Attributes:
        sender_id: The persona that sends it.
        type: The game's message type, such as invite/friend.
        purge: When the server drops the message, as the sender's SDK names it.
        lifetime: The seconds it lives.
        attachments: Its attachments.
        recipient_ids: The personas it goes to.
    """

    sender_id: int
    type: str
    purge: Purge
    lifetime: int
    attachments: list[Attachment]
    recipient_ids: list[int]


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """A message as its boxes hold it; a direct message has number 0.

    Attributes:
        id: The message's number.
        type: The game's message type.
        sender: Who sent it.
        recipients: Who it went to.
        sent: When it was sent.
        expires: When it expires.
        attachments: Its attachments.
    """

    id: int
    type: str
    sender: User
    recipients: list[User]
    sent: datetime
    expires: datetime
    attachments: list[Attachment]


class MessageStore(Protocol):
    """Where messages are kept, with a copy in the sender's outbox and each recipient's inbox.

    A copy goes when its persona deletes it, when a full box makes room, or for a message
    purged on logoff, when its recipient logs off. The message goes once no copy is left; under
    the all-recipients and on-logoff strategies the sender's copy goes with the last recipient's.
    """

    async def send(self, draft: Draft, box_size: int) -> tuple[StoredMessage, Gone]:
        """Store a message with a copy for its sender and each recipient.

        Args:
            draft: The message.
            box_size: The most copies an inbox or outbox holds; the oldest beyond it go.

        Returns:
            The message and the copies that went to make room.
        """

    async def messages(
        self, persona_id: int, *, outbox: bool, ids: Collection[int] = ()
    ) -> list[StoredMessage]:
        """Return the unexpired messages of one of a persona's boxes, oldest first.

        Args:
            persona_id: The persona.
            outbox: Whether to read the messages it sent rather than those it received.
            ids: The messages to read; every one when empty.
        """

    async def delete(self, persona_id: int, ids: Collection[int], *, everywhere: bool) -> Gone:
        """Drop a persona's copies of messages.

        Args:
            persona_id: The persona.
            ids: The messages.
            everywhere: Whether the messages the persona sent also leave every recipient's
                inbox; of a message it received, only its own copy goes.

        Returns:
            The copies that went.
        """

    async def log_off(self, persona_id: int, before: datetime) -> Gone:
        """Drop the copies a persona received of messages purged on logoff.

        Args:
            persona_id: The persona that logged off.
            before: When it logged off; messages sent later stay for its next login.

        Returns:
            The copies that went.
        """


@dataclass(frozen=True, slots=True)
class _Settings:
    notify: bool = True
    types: frozenset[str] = frozenset()
    attachment_types: frozenset[str] = frozenset()


@dataclass(slots=True)
class _Allowance:
    messages: float = SEND_BURST
    since: float = field(default_factory=time.monotonic)

    def take(self) -> bool:
        now = time.monotonic()
        self.messages = min(SEND_BURST, self.messages + (now - self.since) / SEND_INTERVAL)
        self.since = now
        if self.messages < 1:
            return False
        self.messages -= 1
        return True


class MessageService:
    """The xmsg transactions: messages between players, such as invitations.

    A message is stored first and pushed to each recipient that is online and wants it; the
    others find it in their inbox once they log in. Direct messages are only pushed.
    """

    def __init__(
        self,
        store: MessageStore,
        personas: PersonaStore,
        players: Players,
        associations: AssociationStore,
    ) -> None:
        """Serve messages from a store.

        Args:
            store: Where messages are kept.
            personas: Where recipients are looked up.
            players: Where recipients are found online, and where they leave.
            associations: Where the lists are kept on which players block others.
        """
        self._store = store
        self._personas = personas
        self._players = players
        self._associations = associations
        self._settings: dict[GameSession, _Settings] = {}
        self._allowances: dict[GameSession, _Allowance] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        players.on_leave(self._leave)

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("xmsg", "SendMessage"): Route(SendMessage, self.send),
            ("xmsg", "GetMessages"): Route(GetMessages, self.get),
            ("xmsg", "GetMessageAttachments"): Route(GetMessageAttachments, self.attachments),
            ("xmsg", "DeleteMessages"): Route(MessageIds, self.delete),
            ("xmsg", "PurgeMessages"): Route(MessageIds, self.purge),
            ("xmsg", "ModifySettings"): Route(ModifySettings, self.modify_settings),
        }

    async def close(self) -> None:
        """Wait until the messages of personas that logged off are dropped."""
        if self._tasks:
            await asyncio.wait(self._tasks)

    async def send(self, connection: FeslConnection, request: SendMessage) -> SendMessageAnswer:
        """Store a message and push it to its recipients that are online.

        Nothing is stored when no recipient is a persona of the game that takes messages from
        the sender; the answer then carries number 0, as for a direct message.

        Args:
            connection: The FESL connection the request came on.
            request: The message.

        Raises:
            TransactionError: When nobody plays, a dedicated server asks, the message names no
                recipient or too many, or the session sent more messages than it may.
        """
        session, sender = playing_player(connection)
        if not request.to or len(request.to) > MAXIMUM_RECIPIENTS:
            raise TransactionError(PARAMETERS_ERROR)
        if not self._allowances.setdefault(session, _Allowance()).take():
            logger.warning("Refusing a message from persona %d, which sends too many", sender.id)
            raise TransactionError(PARAMETERS_ERROR)
        recipients: dict[int, Persona] = {}
        for persona_id in dict.fromkeys(request.to):
            persona = await self._personas.find_by_id(persona_id)
            if persona is not None and persona.namespace == sender.namespace:
                recipients[persona_id] = persona
        types = blocking(connection.game) if connection.game else []
        for persona_id in await self._associations.listed_by(sender.id, types, recipients):
            del recipients[persona_id]
        lifetime = request.expires if 0 < request.expires <= MAXIMUM_LIFETIME else MAXIMUM_LIFETIME
        stored = request.delivery_type == "standard" and bool(recipients)
        if stored:
            draft = Draft(
                sender.id,
                request.message_type,
                request.purge_strategy,
                lifetime,
                request.attachments,
                list(recipients),
            )
            message, gone = await self._store.send(draft, BOX_SIZE)
            self._announce(gone)
        else:
            sent = datetime.now(UTC).replace(microsecond=0)
            message = StoredMessage(
                0,
                request.message_type,
                _user(sender),
                [_user(persona) for persona in recipients.values()],
                sent,
                sent + timedelta(seconds=lifetime),
                request.attachments,
            )
        pushed = {persona_id for persona_id in recipients if self._deliver(persona_id, message)}
        reached = set(recipients) if stored else pushed
        return SendMessageAnswer(
            message_id=message.id,
            status=[
                RecipientStatus(
                    user_id=persona_id,
                    error=0 if persona_id in reached else UNKNOWN_PERSONA,
                )
                for persona_id in request.to
            ],
        )

    async def get(self, connection: FeslConnection, request: GetMessages) -> GetMessagesAnswer:
        """Return the messages of the player's inbox or outbox.

        Args:
            connection: The FESL connection the request came on.
            request: The box and which of its messages and attachments to return.

        Raises:
            TransactionError: When nobody plays, a dedicated server asks or the request names
                too many messages.
        """
        _, player = playing_player(connection)
        if len(request.message_ids) > MAXIMUM_IDS:
            raise TransactionError(PARAMETERS_ERROR)
        messages = await self._store.messages(
            player.id, outbox=request.box == "outbox", ids=request.message_ids
        )
        types = set(request.message_types)
        return GetMessagesAnswer(
            messages=[
                _info(message, request.attachment_types)
                for message in messages
                if not types or message.type in types
            ]
        )

    async def attachments(
        self, connection: FeslConnection, request: GetMessageAttachments
    ) -> GetMessageAttachmentsAnswer:
        """Return the attachments of a message in one of the player's boxes.

        Args:
            connection: The FESL connection the request came on.
            request: The message and the keys of the attachments; every one when empty.

        Raises:
            TransactionError: When nobody plays, a dedicated server asks or neither box holds
                the message.
        """
        _, player = playing_player(connection)
        ids = [request.message_id]
        found = await self._store.messages(player.id, outbox=False, ids=ids)
        found = found or await self._store.messages(player.id, outbox=True, ids=ids)
        if not found:
            raise TransactionError(UNKNOWN_MESSAGE)
        keys = set(request.keys)
        return GetMessageAttachmentsAnswer(
            attachments=[item for item in found[0].attachments if not keys or item.key in keys]
        )

    async def delete(self, connection: FeslConnection, request: MessageIds) -> Message:
        """Drop messages from the player's boxes.

        Args:
            connection: The FESL connection the request came on.
            request: The messages.

        Raises:
            TransactionError: When nobody plays, a dedicated server asks or the request names
                too many messages.
        """
        await self._drop(connection, request, everywhere=False)
        return Message()

    async def purge(self, connection: FeslConnection, request: MessageIds) -> Message:
        """Drop messages the player sent from every box that holds them.

        Of a message the player received only its own copy goes, so that no recipient takes a
        message from the others.

        Args:
            connection: The FESL connection the request came on.
            request: The messages.

        Raises:
            TransactionError: As for DeleteMessages.
        """
        await self._drop(connection, request, everywhere=True)
        return Message()

    async def modify_settings(self, connection: FeslConnection, request: ModifySettings) -> Message:
        """Keep which arriving messages the player's session hears of, until its persona leaves.

        Args:
            connection: The FESL connection the request came on.
            request: The settings.

        Raises:
            TransactionError: When nobody plays, or a dedicated server asks.
        """
        session, _ = playing_player(connection)
        self._settings[session] = _Settings(
            request.notify_messages,
            frozenset(request.retrieve_message_types),
            frozenset(request.retrieve_attachment_types),
        )
        return Message()

    async def _drop(
        self, connection: FeslConnection, request: MessageIds, *, everywhere: bool
    ) -> None:
        _, player = playing_player(connection)
        if len(request.message_ids) > MAXIMUM_IDS:
            raise TransactionError(PARAMETERS_ERROR)
        self._announce(
            await self._store.delete(player.id, request.message_ids, everywhere=everywhere)
        )

    def _deliver(self, persona_id: int, message: StoredMessage) -> bool:
        session = self._players.playing(persona_id)
        # A game that falls behind with its pushes finds stored messages in its inbox later.
        if session is None or session.busy:
            return False
        settings = self._settings.get(session, _Settings())
        if not settings.notify or (settings.types and message.type not in settings.types):
            return False
        session.push("xmsg", "AsyncMessageEvent", _info(message, settings.attachment_types))
        return True

    def _announce(self, gone: Gone) -> None:
        for persona_id, ids in gone.items():
            session = self._players.playing(persona_id)
            if session is not None:
                session.push("xmsg", "AsyncPurgedEvent", AsyncPurgedEvent(message_ids=ids))

    def _leave(self, session: GameSession, persona: Persona) -> None:
        self._settings.pop(session, None)
        self._allowances.pop(session, None)
        left = datetime.now(UTC)
        task = asyncio.get_running_loop().create_task(self._log_off(persona.id, left))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _log_off(self, persona_id: int, left: datetime) -> None:
        # Nobody waits for this, so a failure can only be logged.
        try:
            gone = await self._store.log_off(persona_id, left)
        except Exception:
            logger.exception("Dropping the messages persona %d had until logoff failed", persona_id)
            return
        self._announce(gone)


def _user(persona: Persona) -> User:
    return User(id=persona.id, name=persona.name)


def _info(message: StoredMessage, attachment_types: Collection[str]) -> MessageInfo:
    return MessageInfo(
        message_id=message.id,
        message_type=message.type,
        time_sent=message.sent,
        expiration=int((message.expires - message.sent).total_seconds()),
        sender=message.sender,
        to=message.recipients,
        attachments=[
            item
            for item in message.attachments
            if not attachment_types or item.type in attachment_types
        ],
    )
