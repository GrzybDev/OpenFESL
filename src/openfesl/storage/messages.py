from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    Select,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    tuple_,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.messages import Draft, Gone, StoredMessage
from openfesl.protocol.xmsg import Attachment, User
from openfesl.storage.models import MessageRecipientRow, MessageRow, PersonaRow

type _Copies = list[tuple[int, int]]

_MESSAGES = select(MessageRow, PersonaRow.name).join(
    PersonaRow, PersonaRow.id == MessageRow.sender_id
)


class SqlMessageStore:
    """Messages in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def send(self, draft: Draft, box_size: int) -> tuple[StoredMessage, Gone]:
        """Store a message with a copy for its sender and each recipient.

        Expired messages go first, so that they hold no room in the boxes.

        Args:
            draft: The message.
            box_size: The most copies an inbox or outbox holds; the oldest beyond it go.

        Returns:
            The message and the copies that went to make room.
        """
        async with self._sessions.begin() as session:
            # Expired messages that another change holds go with a later send, so this one
            # waits for nobody.
            expired = (
                select(MessageRow.id)
                .where(MessageRow.expires_at <= func.now())
                .with_for_update(skip_locked=True)
            )
            await session.execute(delete(MessageRow).where(MessageRow.id.in_(expired)))
        async with self._sessions.begin() as session:
            message_id = await session.scalar(
                insert(MessageRow)
                .values(
                    sender_id=draft.sender_id,
                    type=draft.type,
                    purge=draft.purge,
                    attachments=[item.model_dump() for item in draft.attachments],
                    expires_at=func.now() + timedelta(seconds=draft.lifetime),
                )
                .returning(MessageRow.id)
            )
            await session.execute(
                insert(MessageRecipientRow),
                [
                    {"message_id": message_id, "persona_id": persona_id}
                    for persona_id in draft.recipient_ids
                ],
            )
            gone = await _settle(session, await _make_room(session, draft, box_size))
            [message] = await _load(session, _MESSAGES.where(MessageRow.id == message_id))
        return message, gone

    async def messages(
        self, persona_id: int, *, outbox: bool, ids: Collection[int] = ()
    ) -> list[StoredMessage]:
        """Return the unexpired messages of one of a persona's boxes, oldest first.

        Args:
            persona_id: The persona.
            outbox: Whether to read the messages it sent rather than those it received.
            ids: The messages to read; every one when empty.
        """
        query = _MESSAGES.where(
            MessageRow.expires_at > func.now(),
            _in_outbox(persona_id) if outbox else _received(persona_id),
        )
        if ids:
            query = query.where(MessageRow.id.in_(ids))
        async with self._sessions() as session:
            return await _load(session, query.order_by(MessageRow.id))

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
        async with self._sessions.begin() as session:
            held = await _lock(session, MessageRow.id.in_(ids))
            copies = MessageRecipientRow.persona_id == persona_id
            if everywhere:
                sent = select(MessageRow.id).where(
                    MessageRow.id.in_(held), MessageRow.sender_id == persona_id
                )
                copies |= MessageRecipientRow.message_id.in_(sent)
            inbox = await _empty_inbox(session, MessageRecipientRow.message_id.in_(held), copies)
            outbox = await _empty_outbox(
                session, MessageRow.id.in_(held), MessageRow.sender_id == persona_id
            )
            return await _settle(session, [*inbox, *outbox])

    async def log_off(self, persona_id: int, before: datetime) -> Gone:
        """Drop the copies a persona received of messages purged on logoff.

        Args:
            persona_id: The persona that logged off.
            before: When it logged off; messages sent later stay for its next login.

        Returns:
            The copies that went.
        """
        async with self._sessions.begin() as session:
            held = await _lock(
                session,
                MessageRow.purge == "on-logoff",
                MessageRow.sent_at <= before,
                _received(persona_id),
            )
            copies = await _empty_inbox(
                session,
                MessageRecipientRow.persona_id == persona_id,
                MessageRecipientRow.message_id.in_(held),
            )
            return await _settle(session, copies)


async def _lock(session: AsyncSession, *where: ColumnElement[bool]) -> list[int]:
    # Every change of copies first locks their messages in one statement and in id order, so
    # that two changes never wait for each other in a circle, and holders that drop their
    # copies at the same moment see each other drop them and let the message go.
    held = await session.scalars(
        select(MessageRow.id).where(*where).order_by(MessageRow.id).with_for_update()
    )
    return list(held)


async def _make_room(session: AsyncSession, draft: Draft, size: int) -> _Copies:
    # The oldest copies beyond the size leave the recipients' inboxes and the sender's outbox.
    inboxes = (
        select(
            MessageRecipientRow.message_id,
            MessageRecipientRow.persona_id,
            func.row_number()
            .over(
                partition_by=MessageRecipientRow.persona_id,
                order_by=MessageRecipientRow.message_id.desc(),
            )
            .label("place"),
        )
        .where(MessageRecipientRow.persona_id.in_(draft.recipient_ids), MessageRecipientRow.inbox)
        .subquery()
    )
    outbox = (
        select(MessageRow.id, func.row_number().over(order_by=MessageRow.id.desc()).label("place"))
        .where(_in_outbox(draft.sender_id))
        .subquery()
    )
    crowded = inboxes.c.place > size
    received = select(inboxes.c.message_id, inboxes.c.persona_id).where(crowded)
    sent = select(outbox.c.id).where(outbox.c.place > size)
    held = await _lock(
        session,
        MessageRow.id.in_(select(inboxes.c.message_id).where(crowded)) | MessageRow.id.in_(sent),
    )
    if not held:
        return []
    copy = tuple_(MessageRecipientRow.message_id, MessageRecipientRow.persona_id)
    inbox = await _empty_inbox(
        session, MessageRecipientRow.message_id.in_(held), copy.in_(received)
    )
    outbox_copies = await _empty_outbox(session, MessageRow.id.in_(held), MessageRow.id.in_(sent))
    return [*inbox, *outbox_copies]


async def _empty_inbox(session: AsyncSession, *where: ColumnElement[bool]) -> _Copies:
    rows = await session.execute(
        update(MessageRecipientRow)
        .where(*where, MessageRecipientRow.inbox)
        .values(inbox=False)
        .returning(MessageRecipientRow.message_id, MessageRecipientRow.persona_id)
    )
    return list(rows)


async def _empty_outbox(session: AsyncSession, *where: ColumnElement[bool]) -> _Copies:
    rows = await session.execute(
        update(MessageRow)
        .where(*where, MessageRow.outbox)
        .values(outbox=False)
        .returning(MessageRow.id, MessageRow.sender_id)
    )
    return list(rows)


async def _settle(session: AsyncSession, copies: _Copies) -> Gone:
    # A message goes once no copy is left; the sender's copy goes with the last recipient's,
    # unless the strategy is all, which keeps a message until everyone dropped it.
    if not copies:
        return {}
    gone: Gone = {}
    for message_id, persona_id in copies:
        gone.setdefault(persona_id, []).append(message_id)
    held = exists().where(
        MessageRecipientRow.message_id == MessageRow.id, MessageRecipientRow.inbox
    )
    deleted = await session.execute(
        delete(MessageRow)
        .where(
            MessageRow.id.in_({message_id for message_id, _ in copies}),
            ~held,
            or_(~MessageRow.outbox, MessageRow.purge != "all"),
        )
        .returning(MessageRow.id, MessageRow.sender_id, MessageRow.outbox)
    )
    for message_id, sender_id, outbox in deleted:
        if outbox:
            gone.setdefault(sender_id, []).append(message_id)
    return gone


async def _load(session: AsyncSession, query: Select[MessageRow, str]) -> list[StoredMessage]:
    rows = list(await session.execute(query))
    if not rows:
        return []
    recipients: dict[int, list[User]] = {}
    found = await session.execute(
        select(MessageRecipientRow.message_id, PersonaRow.id, PersonaRow.name)
        .join(PersonaRow, PersonaRow.id == MessageRecipientRow.persona_id)
        .where(MessageRecipientRow.message_id.in_([row.id for row, _ in rows]))
        .order_by(PersonaRow.id)
    )
    for message_id, persona_id, name in found:
        recipients.setdefault(message_id, []).append(User(id=persona_id, name=name))
    return [
        StoredMessage(
            row.id,
            row.type,
            User(id=row.sender_id, name=sender),
            recipients.get(row.id, []),
            row.sent_at,
            row.expires_at,
            [Attachment.model_validate(item) for item in row.attachments],
        )
        for row, sender in rows
    ]


def _in_outbox(persona_id: int) -> ColumnElement[bool]:
    return (MessageRow.sender_id == persona_id) & MessageRow.outbox


def _received(persona_id: int) -> ColumnElement[bool]:
    return exists().where(
        MessageRecipientRow.message_id == MessageRow.id,
        MessageRecipientRow.persona_id == persona_id,
        MessageRecipientRow.inbox,
    )
