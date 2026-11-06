from sqlalchemy import ColumnElement, Select, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import outerjoin

from openfesl.core.accounts import AccountState
from openfesl.core.personas import PersonaState
from openfesl.core.relationships import (
    Found,
    Matches,
    Party,
    Refusal,
    Relationship,
    RelationshipRefused,
)
from openfesl.core.stats import PERSONA, bucket_of
from openfesl.protocol.rank import Period
from openfesl.storage.models import AccountRow, GameServerRow, PersonaRow, RelationshipRow, StatRow
from openfesl.storage.personas import like
from openfesl.storage.stats import in_period

_SIDE = ("game", "tag", "owner_id", "owner_server", "member_id", "member_server")
# Each side with its member's name: an account's display name, or the name a server last listed
# its game under when it goes by the game's persistent id, or else the name it goes by.
_MEMBERS = outerjoin(
    RelationshipRow, AccountRow, AccountRow.id == RelationshipRow.member_id
).outerjoin(GameServerRow, GameServerRow.ugid == RelationshipRow.member_server)
_NAME = func.coalesce(AccountRow.display_name, GameServerRow.name, RelationshipRow.member_server)


class SqlRelationshipStore:
    """Relationships of accounts and dedicated servers in PostgreSQL, one row for each side."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def roster(self, game: str, owner: Party) -> list[Relationship]:
        """Return the relationships of one side in a game with servers and accounts in use.

        Args:
            game: The game's id.
            owner: The side.

        Returns:
            The relationships, oldest first.
        """
        async with self._sessions() as session:
            return await _sides(session, RelationshipRow.game == game, *_owned_by(owner))

    async def increase(
        self, game: str, tag: str, owner: Party, member: Party, most: int
    ) -> Relationship:
        """Ask another side for a relationship, or accept the one it asked for.

        Args:
            game: The game's id.
            tag: The list it is on.
            owner: The side that asks or accepts.
            member: The other side.
            most: The most relationships of the tag the asking side may ask for or accept.

        Returns:
            The relationship as the asking side sees it now.

        Raises:
            RelationshipRefused: When either side is an account that others may not ask, as
                one out of use or without a display name, or the asking side has the most
                relationships it may have.
        """
        async with self._sessions.begin() as session:
            askable = await _lock(session, owner, member)
            # An account that others cannot see could ask for what they would never see.
            if owner.account_id is not None and owner.account_id not in askable:
                raise RelationshipRefused(Refusal.NAMELESS)
            if member.account_id is not None and member.account_id not in askable:
                raise RelationshipRefused(Refusal.UNKNOWN_USER)
            agrees = await session.scalar(
                select(RelationshipRow.owner_agrees).where(*_side(game, tag, owner, member))
            )
            if not agrees:
                if await _agreed(session, game, tag, owner) >= most:
                    raise RelationshipRefused(Refusal.LIST_FULL)
                # The side that changed it has seen the change; the other side has not.
                await _upsert(session, game, tag, owner, member, owner_agrees=True, new=False)
                await _upsert(session, game, tag, member, owner, member_agrees=True, new=True)
            [relationship] = await _sides(session, *_side(game, tag, owner, member))
            return relationship

    async def decrease(
        self, game: str, tag: str, owner: Party, member: Party, *, ends: bool
    ) -> Relationship | None:
        """Withdraw one side's agreement to a relationship, or end it on both sides.

        A relationship that neither side agrees to is gone.

        Args:
            game: The game's id.
            tag: The list it is on.
            owner: The side that withdraws.
            member: The other side.
            ends: Whether the relationship ends on both sides, whoever asked for it.

        Returns:
            The relationship as the withdrawing side sees it now; none when there was none.
        """
        async with self._sessions.begin() as session:
            withdrawn = (
                await session.execute(
                    select(RelationshipRow.id, _NAME, func.now())
                    .select_from(_MEMBERS)
                    .where(*_side(game, tag, owner, member))
                )
            ).first()
            if withdrawn is None:
                return None
            if not ends:
                await _upsert(session, game, tag, owner, member, owner_agrees=False, new=False)
                await _upsert(session, game, tag, member, owner, member_agrees=False, new=True)
            pair = or_(
                and_(*_side(game, tag, owner, member)), and_(*_side(game, tag, member, owner))
            )
            agreed = or_(RelationshipRow.owner_agrees, RelationshipRow.member_agrees)
            gone = (pair,) if ends else (pair, ~agreed)
            await session.execute(delete(RelationshipRow).where(*gone))
            kept = await _sides(session, *_side(game, tag, owner, member))
            side, name, now = withdrawn
            ended = Relationship(side, tag, member, name or "", False, False, False, now)
            return kept[0] if kept else ended

    async def acknowledge(self, game: str, owner: Party, relationship_id: int) -> bool:
        """Mark one side of a relationship seen, so that it is no longer new to that side.

        Args:
            game: The game's id.
            owner: The side.
            relationship_id: The number the side names the relationship by.

        Returns:
            Whether the side has such a relationship.
        """
        async with self._sessions.begin() as session:
            seen = await session.scalar(
                update(RelationshipRow)
                .where(
                    RelationshipRow.id == relationship_id,
                    RelationshipRow.game == game,
                    *_owned_by(owner),
                )
                .values(new=False)
                .returning(RelationshipRow.id)
            )
            return seen is not None

    async def search(self, namespace: str, pattern: str, limit: int) -> list[Found]:
        """Return the accounts that may be asked, by display name, and their heroes by name.

        Args:
            namespace: The namespace of the game's heroes.
            pattern: The start of the names, where * stands for any run of characters; capitals
                do not matter.
            limit: The most names to return, by name.
        """
        start = like(f"{pattern}*")
        async with self._sessions() as session:
            accounts = await session.execute(
                _players(start).order_by(func.lower(AccountRow.display_name)).limit(limit)
            )
            heroes = await session.execute(
                _heroes(namespace, start).order_by(func.lower(PersonaRow.name)).limit(limit)
            )
            found = [Found(account_id, name or "", False) for account_id, name in accounts]
            found += [Found(account_id, name, True) for account_id, name, _ in heroes]
        return sorted(found, key=lambda each: each.name.casefold())[:limit]

    async def matches(
        self, namespace: str, pattern: str, level: tuple[str, str], heroes: slice, players: slice
    ) -> Matches:
        """Return a page of the heroes, by level, and of the accounts that may be asked, by name.

        Args:
            namespace: The namespace of the game's heroes.
            pattern: The start of the names, where * stands for any run of characters; capitals
                do not matter.
            level: The partition of the game's stats and the stat of a hero's level.
            heroes: Which of the heroes to return, the highest level first.
            players: Which of the accounts to return, by display name.
        """
        start = like(f"{pattern}*")
        partition, key = level
        bucket = bucket_of(Period.ALL_TIME, 0)
        named = _players(start)
        hero = _heroes(namespace, start).outerjoin(
            StatRow,
            and_(
                *in_period(StatRow, partition, bucket),
                StatRow.owner_type == PERSONA,
                StatRow.owner_id == PersonaRow.id,
                StatRow.key == key,
            ),
        )
        async with self._sessions() as session:
            hero_count = await session.scalar(select(func.count()).select_from(hero.subquery()))
            player_count = await session.scalar(select(func.count()).select_from(named.subquery()))
            found_heroes = await session.execute(
                hero.order_by(func.coalesce(StatRow.value, 1).desc(), func.lower(PersonaRow.name))
                .offset(heroes.start)
                .limit(heroes.stop - heroes.start)
            )
            found_players = await session.execute(
                named.order_by(func.lower(AccountRow.display_name))
                .offset(players.start)
                .limit(players.stop - players.start)
            )
        return Matches(
            [Found(account, name, True, player or "") for account, name, player in found_heroes],
            hero_count or 0,
            [Found(account, name or "", False, name or "") for account, name in found_players],
            player_count or 0,
        )


async def _lock(session: AsyncSession, *parties: Party) -> set[int]:
    # Changes of the same accounts' relationships wait for each other, locked in id order so
    # that two accounts asking each other at once never wait on each other; the key stays free
    # for rows that refer to the accounts. Returns the accounts that others may ask.
    account_ids = [party.account_id for party in parties if party.account_id is not None]
    rows = await session.execute(
        select(AccountRow.id, and_(*askable_accounts()))
        .where(AccountRow.id.in_(account_ids))
        .order_by(AccountRow.id)
        .with_for_update(key_share=True)
    )
    return {account_id for account_id, askable in rows if askable}


async def _agreed(session: AsyncSession, game: str, tag: str, owner: Party) -> int:
    count = await session.scalar(
        select(func.count()).where(
            RelationshipRow.game == game,
            RelationshipRow.tag == tag,
            *_owned_by(owner),
            RelationshipRow.owner_agrees,
        )
    )
    return count or 0


async def _upsert(
    session: AsyncSession, game: str, tag: str, owner: Party, member: Party, **changes: bool
) -> None:
    columns = (game, tag, owner.account_id, owner.server, member.account_id, member.server)
    # A new side agrees to nothing that the change does not say.
    agreeing = {"owner_agrees": False, "member_agrees": False, **changes}
    await session.execute(
        insert(RelationshipRow)
        .values({**dict(zip(_SIDE, columns, strict=True)), **agreeing})
        .on_conflict_do_update(index_elements=_SIDE, set_={**changes, "modified_at": func.now()})
    )


async def _sides(session: AsyncSession, *where: ColumnElement[bool]) -> list[Relationship]:
    rows = await session.execute(
        select(RelationshipRow, _NAME)
        .select_from(_MEMBERS)
        .where(*where, or_(RelationshipRow.member_server.is_not(None), and_(*askable_accounts())))
        .order_by(RelationshipRow.created_at, RelationshipRow.id)
    )
    return [
        Relationship(
            row.id,
            row.tag,
            Party(row.member_id, row.member_server),
            name or "",
            row.owner_agrees,
            row.member_agrees,
            row.new,
            row.modified_at,
        )
        for row, name in rows
    ]


def _players(start: str) -> Select[int, str | None]:
    # The accounts that others may ask, by a start of their display names.
    return select(AccountRow.id, AccountRow.display_name).where(
        AccountRow.display_name.ilike(start, escape="\\"), *askable_accounts()
    )


def _heroes(namespace: str, start: str) -> Select[int, str, str | None]:
    # The heroes in play of the accounts that others may ask, by a start of their names.
    return (
        select(PersonaRow.account_id, PersonaRow.name, AccountRow.display_name)
        .join(AccountRow, AccountRow.id == PersonaRow.account_id)
        .where(
            PersonaRow.namespace == namespace,
            PersonaRow.name.ilike(start, escape="\\"),
            PersonaRow.disabled_at.is_(None),
            PersonaRow.state == PersonaState.ACTIVE,
            *askable_accounts(),
        )
    )


def askable_accounts() -> tuple[ColumnElement[bool], ...]:
    """Return the conditions of the accounts that others see and may ask as friends.

    They are in use, as logins also take one whose state has ended; named; and players' own.
    """
    return (
        or_(AccountRow.state == AccountState.ACTIVE, AccountRow.state_until <= func.now()),
        AccountRow.display_name.is_not(None),
        AccountRow.server.is_(False),
    )


def _owned_by(owner: Party) -> tuple[ColumnElement[bool], ...]:
    return (
        RelationshipRow.owner_id == owner.account_id,
        RelationshipRow.owner_server == owner.server,
    )


def _side(game: str, tag: str, owner: Party, member: Party) -> tuple[ColumnElement[bool], ...]:
    return (
        RelationshipRow.game == game,
        RelationshipRow.tag == tag,
        *_owned_by(owner),
        RelationshipRow.member_id == member.account_id,
        RelationshipRow.member_server == member.server,
    )
