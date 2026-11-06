from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.claw import ClawRefusal, ClawRefused, Grab, NewGrab
from openfesl.core.entitlements import ACTIVE
from openfesl.core.inventory import Held, Ownership
from openfesl.core.personas import PersonaState
from openfesl.storage.entitlements import add_item, locked_item
from openfesl.storage.models import AccountRow, ClawGrabRow, PersonaRow, PurchaseRow
from openfesl.storage.purchases import PURCHASE
from openfesl.storage.relationships import askable_accounts
from openfesl.storage.wallets import spend


class SqlClawStore:
    """The grabs of the games' Claws in PostgreSQL, their prizes given and paid at once."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def free_grabs(self, game: str, account_id: int, day: date) -> list[Grab]:
        """Return the free grabs an account made on a day, the latest first.

        Args:
            game: The game's data-file name.
            account_id: The account.
            day: The rewards' day.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(ClawGrabRow, PersonaRow.name)
                .join(PersonaRow, PersonaRow.id == ClawGrabRow.persona_id)
                .where(
                    ClawGrabRow.game == game,
                    ClawGrabRow.account_id == account_id,
                    ClawGrabRow.day == day,
                    ClawGrabRow.free.is_not(None),
                )
                .order_by(ClawGrabRow.id.desc())
            )
            return [_grab(row, name) for row, name in rows]

    async def grab(self, grab: NewGrab) -> tuple[Held | None, Held]:
        """Record a grab and give its prize, paying a paid one, all of it or none.

        Args:
            grab: The grab.

        Returns:
            How the hero held the item before, none when it held none, and how it holds it now.

        Raises:
            ClawRefused: When the day's free grabs are used, also by another grab at the same
                time, the wallet holds less than the price, or the hero owns the item for good.
        """
        persona, product = grab.persona, grab.product
        async with self._sessions.begin() as session:
            number = None
            if grab.free:
                number = 1 + await _free_count(session, grab)
                if number > grab.free:
                    raise ClawRefused(ClawRefusal.USED)
            # Recorded first, so that a free grab at the same time waits for this one and fails.
            recorded = await session.scalar(
                insert(ClawGrabRow)
                .values(
                    game=grab.game,
                    account_id=persona.account_id,
                    persona_id=persona.id,
                    day=grab.day,
                    free=number,
                    item_id=product.item_id,
                    ownership=product.ownership.written,
                    rare=grab.rare,
                )
                .on_conflict_do_nothing()
                .returning(ClawGrabRow.id)
            )
            if recorded is None:
                raise ClawRefused(ClawRefusal.USED)
            row = await locked_item(session, persona.id, product.item_id)
            now = datetime.now(UTC)
            before = None
            if row and row.status == ACTIVE and (row.expires_at is None or row.expires_at > now):
                before = Held(row.tag.partition("-")[2], row.expires_at, row.use_count)
            after = await add_item(
                session, persona.account_id, persona.id, product.item_id, product.ownership
            )
            if after is None:
                raise ClawRefused(ClawRefusal.OWNED)
            if grab.wallet:
                if await spend(session, grab.wallet, grab.price, PURCHASE) is None:
                    raise ClawRefused(ClawRefusal.FUNDS)
                session.add(
                    PurchaseRow(
                        game=grab.game,
                        account_id=persona.account_id,
                        persona_id=persona.id,
                        product_id=grab.name,
                        kind="claw",
                        currency=grab.wallet.currency,
                        price=grab.price,
                        outcome={"items": [product.item_id]},
                    )
                )
            return before, after

    async def feed(self, game: str, limit: int) -> list[Grab]:
        """Return the latest rare prizes of the heroes that others see, the latest first.

        Args:
            game: The game's data-file name.
            limit: The most to return.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(ClawGrabRow, PersonaRow.name)
                .join(PersonaRow, PersonaRow.id == ClawGrabRow.persona_id)
                .join(AccountRow, AccountRow.id == ClawGrabRow.account_id)
                .where(
                    ClawGrabRow.game == game,
                    ClawGrabRow.rare,
                    PersonaRow.state == PersonaState.ACTIVE,
                    *askable_accounts(),
                )
                .order_by(ClawGrabRow.created_at.desc(), ClawGrabRow.id.desc())
                .limit(limit)
            )
            return [_grab(row, name) for row, name in rows]

    async def counts(
        self, game: str, day: date, since: datetime, currency: str
    ) -> tuple[int, int, int]:
        """Return how many free and paid grabs were made on a day, and what the paid ones cost.

        Args:
            game: The game's data-file name.
            day: The rewards' day.
            since: When the day started.
            currency: The currency whose spending is counted.
        """
        async with self._sessions() as session:
            free, paid = (
                await session.execute(
                    select(
                        func.count().filter(ClawGrabRow.free.is_not(None)),
                        func.count().filter(ClawGrabRow.free.is_(None)),
                    ).where(ClawGrabRow.game == game, ClawGrabRow.day == day)
                )
            ).one()
            spent = await session.scalar(
                select(func.coalesce(func.sum(PurchaseRow.price), 0)).where(
                    PurchaseRow.game == game,
                    PurchaseRow.kind == "claw",
                    PurchaseRow.currency == currency,
                    PurchaseRow.created_at >= since,
                )
            )
            return int(free), int(paid), int(spent or 0)


async def _free_count(session: AsyncSession, grab: NewGrab) -> int:
    count = await session.scalar(
        select(func.count()).where(
            ClawGrabRow.game == grab.game,
            ClawGrabRow.account_id == grab.persona.account_id,
            ClawGrabRow.day == grab.day,
            ClawGrabRow.free.is_not(None),
        )
    )
    return int(count or 0)


def _grab(row: ClawGrabRow, hero: str) -> Grab:
    ownership = Ownership.read(row.ownership)
    return Grab(
        row.persona_id,
        hero,
        row.item_id,
        ownership,
        row.rare,
        row.free is None,
        row.created_at,
    )
