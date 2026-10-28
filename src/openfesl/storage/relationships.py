from sqlalchemy import ColumnElement, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.accounts import AccountState
from openfesl.core.relationships import Refusal, Relationship, RelationshipRefused
from openfesl.storage.models import AccountRow, RelationshipRow

_SIDE = ("game", "tag", "owner_id", "member_id")


class SqlRelationshipStore:
    """Relationships between accounts in PostgreSQL, one row for each side."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def roster(self, game: str, account_id: int) -> list[Relationship]:
        """Return an account's relationships in a game with accounts that are in use, oldest first.

        Args:
            game: The game's id.
            account_id: The account.
        """
        async with self._sessions() as session:
            return await _sides(
                session, RelationshipRow.game == game, RelationshipRow.owner_id == account_id
            )

    async def increase(
        self, game: str, tag: str, account_id: int, member_id: int, most: int
    ) -> Relationship:
        """Ask another account for a relationship, or accept the one it asked for.

        Args:
            game: The game's id.
            tag: The list it is on.
            account_id: The account that asks or accepts.
            member_id: The other account.
            most: The most relationships of the tag the account may ask for or accept.

        Returns:
            The relationship as the asking account sees it now.

        Raises:
            RelationshipRefused: When either account is not one that others may ask, as one
                out of use or without a display name, or the asking account has the most
                relationships it may have.
        """
        async with self._sessions.begin() as session:
            askable = await _lock(session, account_id, member_id)
            # An account that others cannot see could ask for what they would never see.
            if account_id not in askable:
                raise RelationshipRefused(Refusal.NAMELESS)
            if member_id not in askable:
                raise RelationshipRefused(Refusal.UNKNOWN_USER)
            agrees = await session.scalar(
                select(RelationshipRow.owner_agrees).where(*_side(game, tag, account_id, member_id))
            )
            if not agrees:
                if await _agreed(session, game, tag, account_id) >= most:
                    raise RelationshipRefused(Refusal.LIST_FULL)
                # The side that changed it has seen the change; the other side has not.
                await _upsert(
                    session, (game, tag, account_id, member_id), owner_agrees=True, new=False
                )
                await _upsert(
                    session, (game, tag, member_id, account_id), member_agrees=True, new=True
                )
            [relationship] = await _sides(session, *_side(game, tag, account_id, member_id))
            return relationship

    async def decrease(
        self, game: str, tag: str, account_id: int, member_id: int
    ) -> Relationship | None:
        """End a relationship on both sides, whoever asked for it.

        Args:
            game: The game's id.
            tag: The list it is on.
            account_id: The account that ends it.
            member_id: The other account.

        Returns:
            The relationship as the account sees it now that neither side wants it; none when
            there was none.
        """
        async with self._sessions.begin() as session:
            ended = (
                await session.execute(
                    select(RelationshipRow.id, AccountRow.display_name, func.now())
                    .join(AccountRow, AccountRow.id == RelationshipRow.member_id)
                    .where(*_side(game, tag, account_id, member_id))
                )
            ).first()
            if ended is None:
                return None
            await session.execute(
                delete(RelationshipRow).where(
                    or_(
                        and_(*_side(game, tag, account_id, member_id)),
                        and_(*_side(game, tag, member_id, account_id)),
                    )
                )
            )
            side, name, now = ended
            return Relationship(side, tag, member_id, name or "", False, False, False, now)

    async def acknowledge(self, game: str, account_id: int, relationship_id: int) -> bool:
        """Mark an account's side of a relationship seen, so that it is no longer new to it.

        Args:
            game: The game's id.
            account_id: The account.
            relationship_id: The number the account names the relationship by.

        Returns:
            Whether the account has such a relationship.
        """
        async with self._sessions.begin() as session:
            seen = await session.scalar(
                update(RelationshipRow)
                .where(
                    RelationshipRow.id == relationship_id,
                    RelationshipRow.game == game,
                    RelationshipRow.owner_id == account_id,
                )
                .values(new=False)
                .returning(RelationshipRow.id)
            )
            return seen is not None


async def _lock(session: AsyncSession, *account_ids: int) -> set[int]:
    # Changes of the same accounts' relationships wait for each other, locked in id order so
    # that two accounts asking each other at once never wait on each other; the key stays free
    # for rows that refer to the accounts. Returns the accounts that others may ask.
    rows = await session.execute(
        select(AccountRow.id, and_(*_askable()))
        .where(AccountRow.id.in_(account_ids))
        .order_by(AccountRow.id)
        .with_for_update(key_share=True)
    )
    return {account_id for account_id, askable in rows if askable}


async def _agreed(session: AsyncSession, game: str, tag: str, account_id: int) -> int:
    count = await session.scalar(
        select(func.count()).where(
            RelationshipRow.game == game,
            RelationshipRow.tag == tag,
            RelationshipRow.owner_id == account_id,
            RelationshipRow.owner_agrees,
        )
    )
    return count or 0


async def _upsert(session: AsyncSession, side: tuple[str, str, int, int], **changes: bool) -> None:
    # A new side agrees to nothing that the change does not say.
    agreeing = {"owner_agrees": False, "member_agrees": False, **changes}
    await session.execute(
        insert(RelationshipRow)
        .values({**dict(zip(_SIDE, side, strict=True)), **agreeing})
        .on_conflict_do_update(index_elements=_SIDE, set_={**changes, "modified_at": func.now()})
    )


async def _sides(session: AsyncSession, *where: ColumnElement[bool]) -> list[Relationship]:
    rows = await session.execute(
        select(
            RelationshipRow.id,
            RelationshipRow.tag,
            RelationshipRow.member_id,
            AccountRow.display_name,
            RelationshipRow.owner_agrees,
            RelationshipRow.member_agrees,
            RelationshipRow.new,
            RelationshipRow.modified_at,
        )
        .join(AccountRow, AccountRow.id == RelationshipRow.member_id)
        .where(*where, *_askable())
        .order_by(RelationshipRow.created_at, RelationshipRow.id)
    )
    return [
        Relationship(side, tag, member_id, name or "", own, theirs, new, modified)
        for side, tag, member_id, name, own, theirs, new, modified in rows
    ]


def _askable() -> tuple[ColumnElement[bool], ...]:
    # The accounts that others see and may ask: in use, as logins also take one whose state has
    # ended; named; and players' own.
    return (
        or_(AccountRow.state == AccountState.ACTIVE, AccountRow.state_until <= func.now()),
        AccountRow.display_name.is_not(None),
        AccountRow.server.is_(False),
    )


def _side(game: str, tag: str, owner_id: int, member_id: int) -> tuple[ColumnElement[bool], ...]:
    return (
        RelationshipRow.game == game,
        RelationshipRow.tag == tag,
        RelationshipRow.owner_id == owner_id,
        RelationshipRow.member_id == member_id,
    )
