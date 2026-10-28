from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.purchases import ALREADY_OWNED, INSUFFICIENT_FUNDS, Purchase, PurchaseRefused
from openfesl.storage.entitlements import add_item
from openfesl.storage.models import PurchaseRow
from openfesl.storage.wallets import spend

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
