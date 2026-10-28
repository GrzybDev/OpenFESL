from collections.abc import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.wallets import Wallet
from openfesl.storage.models import WalletEntryRow, WalletRow

# The ledger's reason for the amount that a wallet opens with.
ALLOWANCE = "allowance"


class SqlWalletStore:
    """Wallets and the ledgers of their changes in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def balances(self, wallets: Sequence[Wallet]) -> list[int]:
        """Return the balances of wallets, opening those that are new with their allowance.

        Args:
            wallets: The wallets.
        """
        if not wallets:
            return []
        async with self._sessions.begin() as session:
            # Only the transaction that opens a wallet records its allowance: one that meets the
            # wallet being opened waits for the other to commit and opens nothing.
            opened = await session.execute(
                insert(WalletRow)
                .values(
                    [
                        {
                            "game": wallet.game,
                            "currency": wallet.currency,
                            "account_id": wallet.account_id,
                            "persona_id": wallet.persona_id,
                            "balance": wallet.allowance,
                        }
                        for wallet in wallets
                    ]
                )
                .on_conflict_do_nothing()
                .returning(WalletRow.id, WalletRow.balance)
            )
            entries = [
                {"wallet_id": wallet_id, "delta": balance, "balance": balance, "reason": ALLOWANCE}
                for wallet_id, balance in opened
                if balance
            ]
            if entries:
                await session.execute(insert(WalletEntryRow).values(entries))
            held = await session.execute(
                select(
                    WalletRow.game,
                    WalletRow.currency,
                    WalletRow.account_id,
                    WalletRow.persona_id,
                    WalletRow.balance,
                ).where(
                    or_(
                        *(
                            and_(
                                WalletRow.game == wallet.game,
                                WalletRow.currency == wallet.currency,
                                WalletRow.account_id == wallet.account_id,
                                WalletRow.persona_id.is_not_distinct_from(wallet.persona_id),
                            )
                            for wallet in wallets
                        )
                    )
                )
            )
            found = {
                (row.game, row.currency, row.account_id, row.persona_id): row.balance
                for row in held
            }
            return [
                found[(wallet.game, wallet.currency, wallet.account_id, wallet.persona_id)]
                for wallet in wallets
            ]
