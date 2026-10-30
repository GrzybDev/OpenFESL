from sqlalchemy import Float, and_, cast, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from openfesl.core.hero_boards import Placed, Ranking
from openfesl.core.personas import PersonaState
from openfesl.core.stats import PERSONA, Bucket, bucket_of
from openfesl.protocol.rank import Period
from openfesl.storage.models import AccountRow, PersonaRow, StatRow
from openfesl.storage.relationships import askable_accounts
from openfesl.storage.stats import per, summed


class SqlHeroBoards:
    """The leaderboards of games' heroes, ranked from their stats in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def ranking(
        self, partition: str, namespace: str, ranking: Ranking, bucket: Bucket
    ) -> list[Placed]:
        """Return the lines of the best heroes on a leaderboard, as many as it has places.

        Args:
            partition: The game's partition.
            namespace: The namespace of the game's heroes.
            ranking: How the board ranks heroes and which it lists.
            bucket: The period it ranks.
        """
        over, *under = ranking.keys
        totals = summed(partition, {"over": [over], "under": under, "time": [ranking.time]}, bucket)
        ranked_by = per(totals.c.over, totals.c.under)
        all_time = bucket_of(Period.ALL_TIME, 0)
        looks = [aliased(StatRow) for _ in ranking.look]
        statement = (
            select(
                totals.c.owner_id,
                PersonaRow.name,
                PersonaRow.account_id,
                AccountRow.display_name,
                cast(ranked_by, Float),
                *(look.value for look in looks),
            )
            .join(PersonaRow, PersonaRow.id == totals.c.owner_id)
            .join(AccountRow, AccountRow.id == PersonaRow.account_id)
            .where(
                totals.c.time >= ranking.least,
                PersonaRow.namespace == namespace,
                PersonaRow.state == PersonaState.ACTIVE,
                *askable_accounts(),
            )
            .order_by(ranked_by.desc(), totals.c.owner_id)
            .limit(ranking.places)
        )
        for look, key in zip(looks, ranking.look, strict=True):
            statement = statement.outerjoin(
                look,
                and_(
                    look.partition == partition,
                    look.owner_type == PERSONA,
                    look.owner_id == totals.c.owner_id,
                    look.period == all_time.period,
                    look.period_start == all_time.start,
                    look.key == key,
                ),
            )
        if ranking.within:
            index, number = ranking.within
            statement = statement.where(looks[index].value == number)
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        lines = []
        for place, (hero_id, name, account_id, player, value, *numbers) in enumerate(rows, 1):
            faction, kit, level = (None if number is None else int(number) for number in numbers)
            placed = Placed(hero_id, name, account_id, player, place, value, (faction, kit, level))
            lines.append(placed)
        return lines
