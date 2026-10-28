from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.heroes import HeroRecords, HeroRefused, NewHero, Refusal
from openfesl.core.personas import Persona
from openfesl.core.stats import PERSONA, bucket_of
from openfesl.core.wallets import Wallet
from openfesl.protocol.rank import Period
from openfesl.storage.models import EntitlementRow, HeroAccountRow, PersonaRow, StatRow
from openfesl.storage.personas import persona_of
from openfesl.storage.wallets import spend

# The ledger's reason for a hero's new name.
RENAME = "rename"


class SqlHeroStore:
    """Heroes and the accounts' default heroes in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def records(self, account_id: int, game: str, namespace: str) -> HeroRecords:
        """Return what is kept of an account's heroes in a game.

        Args:
            account_id: The account.
            game: The game's data-file name.
            namespace: The game's persona namespace.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PersonaRow)
                .where(
                    PersonaRow.account_id == account_id,
                    PersonaRow.namespace == namespace,
                    PersonaRow.disabled_at.is_(None),
                )
                .order_by(PersonaRow.id)
            )
            personas = [(persona_of(row), row.created_at) for row in rows]
            default_id = await session.scalar(
                select(HeroAccountRow.default_persona_id).where(
                    HeroAccountRow.account_id == account_id, HeroAccountRow.game == game
                )
            )
        return HeroRecords(personas, default_id)

    async def taken(self, namespace: str, name: str) -> bool:
        """Tell whether a persona has a name, ignoring capitals, those taken out of use too.

        Args:
            namespace: The namespace.
            name: The name.
        """
        async with self._sessions() as session:
            found = await session.scalar(
                select(PersonaRow.id).where(
                    PersonaRow.namespace == namespace,
                    func.lower(PersonaRow.name) == func.lower(name),
                )
            )
        return found is not None

    async def rename(self, persona: Persona, name: str, price: Wallet, amount: int) -> None:
        """Give a hero a new name and take its price from a wallet, both or neither.

        Args:
            persona: The hero's persona.
            name: The new name.
            price: The wallet the price is taken from.
            amount: The price.

        Raises:
            HeroRefused: When the name is taken, also by a request at the same time, or the
                wallet holds less than the price.
        """
        async with self._sessions.begin() as session:
            try:
                # A savepoint, so that a taken name leaves the transaction usable to refuse.
                async with session.begin_nested():
                    await session.execute(
                        update(PersonaRow).where(PersonaRow.id == persona.id).values(name=name)
                    )
            except IntegrityError:
                raise HeroRefused(Refusal.TAKEN) from None
            if await spend(session, price, amount, RENAME) is None:
                raise HeroRefused(Refusal.FUNDS)

    async def set_default(
        self, account_id: int, game: str, namespace: str, persona_id: int
    ) -> bool:
        """Make one of an account's heroes its default hero.

        Args:
            account_id: The account.
            game: The game's data-file name.
            namespace: The game's persona namespace.
            persona_id: The hero's persona.

        Returns:
            Whether the hero is one of the account's.
        """
        async with self._sessions.begin() as session:
            owned = await session.scalar(
                select(PersonaRow.id).where(
                    PersonaRow.id == persona_id,
                    PersonaRow.account_id == account_id,
                    PersonaRow.namespace == namespace,
                    PersonaRow.disabled_at.is_(None),
                )
            )
            if owned is None:
                return False
            chosen = {"account_id": account_id, "game": game, "default_persona_id": persona_id}
            statement = insert(HeroAccountRow).values(chosen)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[HeroAccountRow.account_id, HeroAccountRow.game],
                    set_={"default_persona_id": statement.excluded.default_persona_id},
                )
            )
            return True

    async def create(self, hero: NewHero) -> Persona:
        """Store a hero with its stats and items; the account's first becomes its default.

        Everything is stored at once or nothing is.

        Args:
            hero: The hero.

        Raises:
            HeroRefused: When the account has no free slot or the name is taken, also by a
                request at the same time.
        """
        async with self._sessions.begin() as session:
            # The account's row is locked while its heroes are counted, so two creations at
            # once take one slot after the other.
            await session.execute(
                insert(HeroAccountRow)
                .values(account_id=hero.account_id, game=hero.game)
                .on_conflict_do_nothing()
            )
            account = await session.scalar(
                select(HeroAccountRow)
                .where(
                    HeroAccountRow.account_id == hero.account_id, HeroAccountRow.game == hero.game
                )
                .with_for_update()
            )
            assert account is not None
            heroes = await session.scalar(
                select(func.count()).where(
                    PersonaRow.account_id == hero.account_id,
                    PersonaRow.namespace == hero.namespace,
                    PersonaRow.disabled_at.is_(None),
                )
            )
            if (heroes or 0) >= hero.free_slots:
                raise HeroRefused(Refusal.NO_SLOT)
            # The unique name index decides between two requests for one name.
            row = await session.scalar(
                insert(PersonaRow)
                .values(account_id=hero.account_id, namespace=hero.namespace, name=hero.name)
                .on_conflict_do_nothing()
                .returning(PersonaRow)
            )
            if row is None:
                raise HeroRefused(Refusal.TAKEN)
            bucket = bucket_of(Period.ALL_TIME, 0)
            await session.execute(
                insert(StatRow).values(
                    [
                        {
                            "partition": hero.partition,
                            "owner_type": PERSONA,
                            "owner_id": row.id,
                            "period": bucket.period,
                            "period_start": bucket.start,
                            "key": key,
                            "value": value,
                            "text": text,
                        }
                        for key, (value, text) in hero.stats.items()
                    ]
                )
            )
            if hero.items:
                await session.execute(
                    insert(EntitlementRow).values(
                        [
                            {
                                "account_id": hero.account_id,
                                "persona_id": row.id,
                                "tag": tag,
                                "use_count": uses,
                            }
                            for tag, uses in hero.items.items()
                        ]
                    )
                )
            if account.default_persona_id is None:
                await session.execute(
                    update(HeroAccountRow)
                    .where(
                        HeroAccountRow.account_id == hero.account_id,
                        HeroAccountRow.game == hero.game,
                    )
                    .values(default_persona_id=row.id)
                )
            return persona_of(row)
