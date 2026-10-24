from dataclasses import replace

from sqlalchemy import ColumnElement, delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.associations import Change, Member, OwnedList
from openfesl.storage.models import AssociationRow, PersonaRow

_STAMPS = (AssociationRow.created_at, AssociationRow.modified_at)


class SqlAssociationStore:
    """Association lists in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def members(self, owned: OwnedList) -> list[Member]:
        """Return a list's members whose personas are in use, oldest first.

        Args:
            owned: The list.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(AssociationRow.member_id, PersonaRow.name, *_STAMPS)
                .join(PersonaRow, PersonaRow.id == AssociationRow.member_id)
                .where(*_visible(owned))
                .order_by(AssociationRow.created_at, AssociationRow.member_id)
            )
            return [Member(*row) for row in rows]

    async def count(self, owned: OwnedList) -> int:
        """Return how many members a list holds whose personas are in use.

        Args:
            owned: The list.
        """
        async with self._sessions() as session:
            return await _size(session, owned)

    async def add(
        self, owned: OwnedList, member_id: int, limit: int, *, roll: bool, mutual: bool
    ) -> list[Change] | None:
        """Add a member to a list, or mark it modified when it is a member already.

        Args:
            owned: The list.
            member_id: The persona to add.
            limit: The most members a list holds.
            roll: Whether the owner's full list drops its least recently modified member to
                make room.
            mutual: Whether the member's own list of the type gains the owner too, once the
                member agreed by listing the owner itself.

        Returns:
            The owner's change, then the member's when its list changed, then the member that
            the owner's full list dropped; None when a list is full and nothing changed.
        """
        try:
            async with self._sessions.begin() as session:
                names = await _lock(session, owned.owner_id, member_id)
                added, dropped = await _add(
                    session, owned, member_id, names[member_id], limit, roll
                )
                changes = [added]
                theirs = replace(owned, owner_id=member_id)
                if mutual and await _agreed(session, theirs, owned.owner_id):
                    owner_name = names[owned.owner_id]
                    changes.append(
                        (await _add(session, theirs, owned.owner_id, owner_name, limit))[0]
                    )
                return [*changes, dropped] if dropped else changes
        except _Full:
            return None

    async def delete(self, owned: OwnedList, member_id: int, *, mutual: bool) -> list[Change]:
        """Remove a member from a list.

        Args:
            owned: The list.
            member_id: The persona to remove.
            mutual: Whether the member's own list of the type loses the owner too.

        Returns:
            A change for each list that held the member.
        """
        async with self._sessions.begin() as session:
            names = await _lock(session, owned.owner_id, member_id)
            changes = []
            for target, member in _pairs(owned, member_id, mutual):
                stamps = (
                    await session.execute(
                        delete(AssociationRow)
                        .where(*_of(target), AssociationRow.member_id == member)
                        .returning(*_STAMPS)
                    )
                ).first()
                if stamps is not None:
                    gone = Member(member, names[member], *stamps)
                    size = await _size(session, target)
                    changes.append(Change(target, gone, size, removed=True))
            return changes


class _Full(Exception):
    pass


async def _lock(session: AsyncSession, *persona_ids: int) -> dict[int, str]:
    # Changes to the same personas' lists wait for each other, locked in id order so that two
    # mutual changes never wait on each other.
    rows = await session.execute(
        select(PersonaRow.id, PersonaRow.name)
        .where(PersonaRow.id.in_(persona_ids))
        .order_by(PersonaRow.id)
        .with_for_update()
    )
    return {persona_id: name for persona_id, name in rows}


async def _agreed(session: AsyncSession, theirs: OwnedList, owner_id: int) -> bool:
    listed = exists().where(*_of(theirs), AssociationRow.member_id == owner_id)
    return bool(await session.scalar(select(listed)))


async def _add(
    session: AsyncSession,
    owned: OwnedList,
    member_id: int,
    name: str,
    limit: int,
    roll: bool = False,
) -> tuple[Change, Change | None]:
    entry = (*_of(owned), AssociationRow.member_id == member_id)
    stamps = (
        await session.execute(
            update(AssociationRow).where(*entry).values(modified_at=func.now()).returning(*_STAMPS)
        )
    ).first()
    dropped = None
    if stamps is None:
        if await _size(session, owned) >= limit:
            if not roll or limit <= 0:
                raise _Full
            dropped = await _drop_oldest(session, owned)
        stamps = (
            await session.execute(
                insert(AssociationRow)
                .values(
                    owner_id=owned.owner_id,
                    type=owned.type,
                    partition=owned.partition,
                    member_id=member_id,
                )
                .returning(*_STAMPS)
            )
        ).one()
    return Change(owned, Member(member_id, name, *stamps), await _size(session, owned)), dropped


async def _drop_oldest(session: AsyncSession, owned: OwnedList) -> Change:
    # Members of disabled personas take no room, so the one that makes room is a visible one.
    oldest = (
        await session.execute(
            select(AssociationRow.member_id, PersonaRow.name, *_STAMPS)
            .join(PersonaRow, PersonaRow.id == AssociationRow.member_id)
            .where(*_visible(owned))
            .order_by(AssociationRow.modified_at, AssociationRow.created_at)
            .limit(1)
        )
    ).one()
    member = Member(*oldest)
    await session.execute(
        delete(AssociationRow).where(*_of(owned), AssociationRow.member_id == member.id)
    )
    return Change(owned, member, await _size(session, owned), removed=True)


async def _size(session: AsyncSession, owned: OwnedList) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(AssociationRow)
        .join(PersonaRow, PersonaRow.id == AssociationRow.member_id)
        .where(*_visible(owned))
    )
    return count or 0


def _pairs(owned: OwnedList, member_id: int, mutual: bool) -> list[tuple[OwnedList, int]]:
    pairs = [(owned, member_id)]
    if mutual:
        pairs.append((replace(owned, owner_id=member_id), owned.owner_id))
    return pairs


def _of(owned: OwnedList) -> tuple[ColumnElement[bool], ...]:
    return (
        AssociationRow.owner_id == owned.owner_id,
        AssociationRow.type == owned.type,
        AssociationRow.partition == owned.partition,
    )


def _visible(owned: OwnedList) -> tuple[ColumnElement[bool], ...]:
    return (*_of(owned), PersonaRow.disabled_at.is_(None))
