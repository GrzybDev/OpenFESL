from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.personas import Persona
from openfesl.core.purchases import (
    ALREADY_OWNED,
    INSUFFICIENT_FUNDS,
    Purchase,
    PurchaseRefused,
    Refund,
)
from openfesl.core.wallets import Wallet
from openfesl.storage.entitlements import add_item
from openfesl.storage.models import EntitlementRow, PurchaseRow
from openfesl.storage.wallets import REFUND, credit, spend

# The ledger's reason for what a purchase took from a wallet.
PURCHASE = "purchase"


class SqlPurchaseStore:
    """The purchases made in the games' stores in PostgreSQL, paid and given at once."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def buy(self, purchase: Purchase) -> None:
        """Pay a purchase's price, give the hero its items and record it, all or nothing.

        Args:
            purchase: The purchase.

        Raises:
            PurchaseRefused: When the hero owns an item for good or it was taken away, or the
                wallet holds less than the price, also after another purchase at the same time.
        """
        persona = purchase.persona
        async with self._sessions.begin() as session:
            # The items' rows are locked in one order, so that purchases at once wait in turn.
            added = [
                item_id
                for item_id, gained in sorted(purchase.items, key=lambda item: item[0])
                if await add_item(session, persona.account_id, persona.id, item_id, gained)
            ]
            if not added:
                raise PurchaseRefused(ALREADY_OWNED)
            if await spend(session, purchase.wallet, purchase.price, PURCHASE) is None:
                raise PurchaseRefused(INSUFFICIENT_FUNDS)
            session.add(
                PurchaseRow(
                    game=purchase.game,
                    account_id=persona.account_id,
                    persona_id=persona.id,
                    product_id=purchase.product_id,
                    kind=purchase.kind,
                    currency=purchase.wallet.currency,
                    price=purchase.price,
                    outcome={"items": added},
                )
            )

    async def refund(self, persona: Persona, wallet: Wallet) -> Refund | None:
        """Take back what a hero bought from a wallet and credit what it paid, all or nothing.

        Args:
            persona: The hero.
            wallet: The hero's own wallet, whose purchases are refunded.

        Returns:
            What was refunded, or None when nothing bought from the wallet is left to refund.
        """
        async with self._sessions.begin() as session:
            # Locked, so that a refund at the same time finds them refunded once it may read.
            bought = list(
                await session.scalars(
                    select(PurchaseRow)
                    .where(
                        PurchaseRow.persona_id == persona.id,
                        PurchaseRow.currency == wallet.currency,
                        PurchaseRow.refunded_at.is_(None),
                    )
                    .order_by(PurchaseRow.id)
                    .with_for_update()
                )
            )
            if not bought:
                return None
            items = list(dict.fromkeys(item for row in bought for item in row.outcome["items"]))
            await session.execute(
                delete(EntitlementRow).where(
                    EntitlementRow.persona_id == persona.id,
                    func.split_part(EntitlementRow.tag, "-", 1).in_([str(item) for item in items]),
                )
            )
            await session.execute(
                update(PurchaseRow)
                .where(PurchaseRow.id.in_([row.id for row in bought]))
                .values(refunded_at=func.now())
            )
            balance = await credit(session, wallet, sum(row.price for row in bought), REFUND)
            assert balance is not None
            return Refund(wallet.currency, balance, items)
