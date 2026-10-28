from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.chests import ChestRefusal, ChestRefused
from openfesl.core.personas import Persona
from openfesl.core.purchases import (
    ALREADY_OWNED,
    INSUFFICIENT_FUNDS,
    Purchase,
    PurchaseRefused,
    Refund,
)
from openfesl.core.store import Product
from openfesl.core.wallets import Wallet
from openfesl.storage.entitlements import add_account_item, add_item, use_one
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

    async def buy_for_account(
        self, account_id: int, product_id: str, product: Product, wallet: Wallet, count: int
    ) -> None:
        """Pay a product's price, give the account the item and record it, all or nothing.

        Args:
            account_id: The account.
            product_id: The id the product was bought by.
            product: The product, which the account owns once bought.
            wallet: The account's wallet the price is paid from.
            count: How many of the product are bought at once, for its price that many times.

        Raises:
            PurchaseRefused: When the wallet holds less than the price, also after another
                purchase at the same time.
        """
        tag = product.ownership.tag(product.item_id)
        price = product.price * count
        async with self._sessions.begin() as session:
            await add_account_item(session, account_id, tag, (product.ownership.uses or 1) * count)
            if await spend(session, wallet, price, PURCHASE) is None:
                raise PurchaseRefused(INSUFFICIENT_FUNDS)
            session.add(
                PurchaseRow(
                    game=wallet.game,
                    account_id=account_id,
                    product_id=product_id,
                    kind="product",
                    currency=wallet.currency,
                    price=price,
                    outcome={"items": [product.item_id], "count": count},
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

    async def open_chest(self, persona: Persona, key: str, chest: str, won: Product) -> None:
        """Use up one of an account's keys and one of its hero's chests, and give what it held.

        All changes are made or none.

        Args:
            persona: The hero.
            key: The tag of the account's keys.
            chest: The tag of the hero's chests.
            won: What the chest gives.

        Raises:
            ChestRefused: When the account has no key or the hero no chest, also after another
                opening at the same time, or the hero came to own the item for good meanwhile.
        """
        async with self._sessions.begin() as session:
            if not await use_one(session, persona.account_id, None, key):
                raise ChestRefused(ChestRefusal.NO_KEY)
            if not await use_one(session, persona.account_id, persona.id, chest):
                raise ChestRefused(ChestRefusal.NO_CHEST)
            added = await add_item(
                session, persona.account_id, persona.id, won.item_id, won.ownership
            )
            if added is None:
                raise ChestRefused(ChestRefusal.NOTHING)
