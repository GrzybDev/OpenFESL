from collections.abc import Collection

from sqlalchemy import String, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.reward_claims import Grant
from openfesl.core.rewards import RewardRefusal, RewardRefused
from openfesl.core.stats import PERSONA, Bucket
from openfesl.storage.entitlements import add_account_item, add_item
from openfesl.storage.models import RewardBaselineRow, RewardClaimRow, StatRow
from openfesl.storage.wallets import credit

# The ledger's reason for the Funds that rewards give.
REWARD = "reward"


class SqlRewardStore:
    """The rewards that accounts claimed in PostgreSQL, given as they are recorded."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def claimed(
        self, game: str, account_id: int, keys: Collection[str]
    ) -> set[tuple[str, str | None]]:
        """Return which rewards an account claimed, each by its key and faction.

        Args:
            game: The game's data-file name.
            account_id: The account.
            keys: The keys of the rewards to look at.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(RewardClaimRow.key, RewardClaimRow.faction).where(
                    RewardClaimRow.game == game,
                    RewardClaimRow.account_id == account_id,
                    RewardClaimRow.key.in_(keys),
                )
            )
            return {(key, faction) for key, faction in rows}

    async def baselines(
        self,
        game: str,
        partition: str,
        key: str,
        stat: str,
        bucket: Bucket,
        personas: Collection[int],
    ) -> dict[int, int]:
        """Return the values a stat of heroes had when an event began, taking them on first use.

        The first use takes every hero's value at once, so that a hero without the stat then
        counts from 0; later uses read what it took.

        Args:
            game: The game's data-file name.
            partition: The game's partition, which holds the stats.
            key: The event's key, of its year.
            stat: The stat.
            bucket: The period of the stat's values.
            personas: The heroes whose values to return.

        Returns:
            The values the heroes had, by persona; a hero without one had none.
        """
        taken = select(
            literal(game),
            literal(key),
            literal(stat),
            func.coalesce(
                func.jsonb_object_agg(cast(StatRow.owner_id, String), StatRow.value),
                cast(literal("{}"), JSONB),
            ),
        ).where(
            StatRow.partition == partition,
            StatRow.owner_type == PERSONA,
            StatRow.period == bucket.period,
            StatRow.period_start == bucket.start,
            StatRow.key == stat,
        )
        columns = ["game", "key", "stat", "values"]
        read = select(RewardBaselineRow.values).where(
            RewardBaselineRow.game == game,
            RewardBaselineRow.key == key,
            RewardBaselineRow.stat == stat,
        )
        async with self._sessions.begin() as session:
            values = await session.scalar(read)
            if values is None:
                # Only the first use takes them, also when two take them at once.
                await session.execute(
                    insert(RewardBaselineRow).from_select(columns, taken).on_conflict_do_nothing()
                )
                values = await session.scalar(read)
        held = values or {}
        return {persona: int(held[str(persona)]) for persona in personas if str(persona) in held}

    async def grant(self, grant: Grant) -> list[int]:
        """Record a claim and give what it grants, all of it or none.

        Args:
            grant: What the claim gives.

        Returns:
            The items the hero got, an owned one replaced by the fallback.

        Raises:
            RewardRefused: When the account claimed the reward already, also at the same time.
        """
        account = grant.account_id
        async with self._sessions.begin() as session:
            # Recorded first, so that a claim at the same time waits for this one and finds it.
            claim = await session.scalar(
                insert(RewardClaimRow)
                .values(
                    game=grant.game,
                    account_id=account,
                    key=grant.key,
                    faction=grant.faction,
                    persona_id=grant.hero,
                    outcome={},
                )
                .on_conflict_do_nothing()
                .returning(RewardClaimRow.id)
            )
            if claim is None:
                raise RewardRefused(RewardRefusal.CLAIMED)
            # The account's keys and the hero's items before any wallet, in the order purchases
            # and chest openings lock them, so that one at the same time waits, not deadlocks.
            if grant.keys:
                await add_account_item(session, account, *grant.keys)
            given = await _give(session, grant) if grant.hero is not None else []
            if grant.funds:
                await credit(session, *grant.funds, REWARD)
            outcome = {
                "items": given,
                "funds": grant.funds[1] if grant.funds else 0,
                "keys": grant.keys[1] if grant.keys else 0,
            }
            await session.execute(
                update(RewardClaimRow).where(RewardClaimRow.id == claim).values(outcome=outcome)
            )
            return given


async def _give(session: AsyncSession, grant: Grant) -> list[int]:
    assert grant.hero is not None
    given = []
    # In the items' order, as purchases lock them, so that both wait in turn.
    for item_id, ownership in sorted(grant.items, key=lambda item: item[0]):
        held = await add_item(session, grant.account_id, grant.hero, item_id, ownership)
        if held is None:
            await add_item(session, grant.account_id, grant.hero, *grant.fallback)
        given.append(item_id if held else grant.fallback[0])
    return given
