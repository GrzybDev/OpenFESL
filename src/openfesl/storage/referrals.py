from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, delete, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.games import Game
from openfesl.core.personas import PersonaState
from openfesl.core.referrals import Leader, Reach, Recruit, ReferralCounts
from openfesl.core.rewards import REFERRAL
from openfesl.core.stats import PERSONA, bucket_of, partition_of
from openfesl.protocol.rank import Period
from openfesl.storage.accounts import account_from
from openfesl.storage.admin import action_row
from openfesl.storage.models import AccountRow, PersonaRow, ReferralRow, RewardClaimRow, StatRow
from openfesl.storage.stats import in_period

_RECRUITER = aliased(AccountRow)


class SqlReferralStore:
    """The recruits of personal links in PostgreSQL, with the steps they reached."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def recruits(self, game: Game, recruiter_id: int) -> list[Recruit]:
        """Return the recruits of an account in a game, the newest first.

        Args:
            game: The game, whose heroes count.
            recruiter_id: The recruiter's account.
        """
        chosen = _recruits(game).where(ReferralRow.recruiter_id == recruiter_id)
        return await self._read(game, chosen)

    async def recruited(self, game: Game, recruit_id: int) -> Recruit | None:
        """Return an account as a recruit in a game, if a personal link brought it.

        Args:
            game: The game, whose heroes count.
            recruit_id: The account.
        """
        found = await self._read(game, _recruits(game).where(ReferralRow.recruit_id == recruit_id))
        return found[0] if found else None

    async def reach(self, game: str, recruit_id: int, step: str, reach: Reach) -> None:
        """Record that a recruit's hero reached a step's goal, only the first time it is seen.

        Args:
            game: The game's data-file name.
            recruit_id: The recruit's account.
            step: The step's name.
            reach: When, by which hero and at which level.
        """
        written = {"at": reach.at.isoformat(), "hero": reach.hero, "level": reach.level}
        async with self._sessions.begin() as session:
            await session.execute(
                update(ReferralRow)
                .where(
                    ReferralRow.recruit_id == recruit_id,
                    ReferralRow.game == game,
                    ~ReferralRow.reached.has_key(step),
                )
                .values(reached=ReferralRow.reached.concat(literal({step: written}, JSONB)))
            )

    async def remove(self, game: Game, recruit_id: int, note: str, admin: Admin) -> Recruit | None:
        """Take an account out of the recruits of its recruiter and record why; claims stay.

        Args:
            game: The game.
            recruit_id: The recruit's account.
            note: Why, as the admin wrote it.
            admin: The admin who removed it.

        Returns:
            The recruit as it was; none when it was no recruit, also once removed meanwhile.
        """
        found = await self.recruited(game, recruit_id)
        if found is None:
            return None
        names = [found.account.display_name or found.account.nuid]
        names.append(found.recruiter.display_name or found.recruiter.nuid)
        details = {"game": game.id, "recruiter": found.recruiter.id, "names": names, "note": note}
        async with self._sessions.begin() as session:
            removed = await session.scalar(
                delete(ReferralRow)
                .where(ReferralRow.recruit_id == recruit_id, ReferralRow.game == game.id)
                .returning(ReferralRow.recruit_id)
            )
            if removed is not None:
                session.add(
                    action_row(AdminAction.REFERRAL_REMOVED, str(recruit_id), details, admin)
                )
        return found if removed is not None else None

    async def counts(self, game: str, since: datetime) -> ReferralCounts:
        """Return how many recruits signed up and how many rewards were claimed and wait.

        Args:
            game: The game's data-file name.
            since: The start of the month.
        """
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(
                        ReferralRow.recruit_id,
                        ReferralRow.recruiter_id,
                        ReferralRow.created_at,
                        ReferralRow.reached,
                    ).where(ReferralRow.game == game)
                )
            ).all()
            claims = (
                await session.execute(
                    select(RewardClaimRow.account_id, RewardClaimRow.key, RewardClaimRow.claimed_at)
                    .where(RewardClaimRow.game == game)
                    .where(RewardClaimRow.key.like(f"{REFERRAL}:%"))
                )
            ).all()
        claimed = {(account, key) for account, key, _ in claims}
        # Each step reached waits for both accounts' claims: the recruiter's, which names the
        # recruit, and the recruit's own.
        waiting = sum(
            ((recruiter, f"{REFERRAL}:{recruit}:{step}") not in claimed)
            + ((recruit, f"{REFERRAL}:{step}") not in claimed)
            for recruit, recruiter, _, reached in rows
            for step in reached
        )
        return ReferralCounts(
            sum(created >= since for _, _, created, _ in rows),
            len(rows),
            sum(at >= since for _, _, at in claims),
            waiting,
        )

    async def _read(
        self, game: Game, chosen: Select[ReferralRow, AccountRow, AccountRow]
    ) -> list[Recruit]:
        async with self._sessions() as session:
            rows = (await session.execute(chosen)).all()
            leaders = await _leaders(session, game, [referral.recruit_id for referral, *_ in rows])
        return [
            Recruit(
                account_from(recruit),
                account_from(recruiter),
                referral.created_at,
                {step: _reach(found) for step, found in referral.reached.items()},
                leaders.get(referral.recruit_id),
            )
            for referral, recruit, recruiter in rows
        ]


def _recruits(game: Game) -> Select[ReferralRow, AccountRow, AccountRow]:
    return (
        select(ReferralRow, AccountRow, _RECRUITER)
        .join(AccountRow, AccountRow.id == ReferralRow.recruit_id)
        .join(_RECRUITER, _RECRUITER.id == ReferralRow.recruiter_id)
        .where(ReferralRow.game == game.id)
        .order_by(ReferralRow.created_at.desc(), ReferralRow.recruit_id.desc())
    )


async def _leaders(session: AsyncSession, game: Game, accounts: list[int]) -> dict[int, Leader]:
    # Each account's hero with the highest level, the first made among equals; a hero without a
    # level stat is at level 1, as the site shows it.
    if not accounts or game.heroes is None:
        return {}
    bucket = bucket_of(Period.ALL_TIME, 0)
    rows = await session.execute(
        select(PersonaRow.account_id, PersonaRow.id, PersonaRow.name, StatRow.value)
        .outerjoin(
            StatRow,
            and_(
                *in_period(StatRow, partition_of(game), bucket),
                StatRow.owner_id == PersonaRow.id,
                StatRow.owner_type == PERSONA,
                StatRow.key == game.heroes.stats.level,
            ),
        )
        .where(
            PersonaRow.account_id.in_(accounts),
            PersonaRow.namespace == game.namespace,
            PersonaRow.disabled_at.is_(None),
            PersonaRow.state == PersonaState.ACTIVE,
        )
        .order_by(PersonaRow.id)
    )
    leaders: dict[int, Leader] = {}
    for account_id, persona_id, name, value in rows:
        level = max(int(value or 1), 1)
        if account_id not in leaders or level > leaders[account_id].level:
            leaders[account_id] = Leader(persona_id, name, level)
    return leaders


def _reach(found: dict[str, Any]) -> Reach:
    return Reach(datetime.fromisoformat(found["at"]), found["hero"], found["level"])
