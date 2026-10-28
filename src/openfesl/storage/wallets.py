from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_, func, or_, select, update
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
            await open_wallets(session, wallets)
            return await _held(session, wallets)

    async def peek(self, wallets: Sequence[Wallet]) -> list[int]:
        """Return the balances of wallets without opening any; a new one shows its allowance.

        Args:
            wallets: The wallets.
        """
        if not wallets:
            return []
        async with self._sessions() as session:
            return await _held(session, wallets)

    async def earned(self, wallets: Sequence[Wallet]) -> list[int]:
        """Return what wallets ever received, from their ledgers, without opening any.

        A new wallet counts the allowance it opens with.

        Args:
            wallets: The wallets.
        """
        if not wallets:
            return []
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    WalletRow.game,
                    WalletRow.currency,
                    WalletRow.account_id,
                    WalletRow.persona_id,
                    func.coalesce(func.sum(WalletEntryRow.delta), 0),
                )
                .outerjoin(
                    WalletEntryRow,
                    and_(WalletEntryRow.wallet_id == WalletRow.id, WalletEntryRow.delta > 0),
                )
                .where(_matching(wallets))
                .group_by(WalletRow.id)
            )
            found = {(row[0], row[1], row[2], row[3]): int(row[4]) for row in rows}
        return [found.get(_key(wallet), wallet.allowance) for wallet in wallets]


async def open_wallets(session: AsyncSession, wallets: Sequence[Wallet]) -> None:
    """Open the wallets that are new with their allowance, recorded in their ledgers.

    Only the transaction that opens a wallet records its allowance: one that meets the wallet being
    opened waits for the other to commit and opens nothing.

    Args:
        session: The transaction to open them in.
        wallets: The wallets, at least one.
    """
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


async def spend(session: AsyncSession, wallet: Wallet, amount: int, reason: str) -> int | None:
    """Take an amount from a wallet, opening it first, and record it in its ledger.

    The balance is checked and changed in one statement, so two spendings at once never take it
    below zero.

    Args:
        session: The transaction to spend in, which is rolled back when anything after fails.
        wallet: The wallet.
        amount: The amount.
        reason: Why, as the ledger keeps it.

    Returns:
        The balance left, or None when the wallet holds less than the amount.
    """
    await open_wallets(session, [wallet])
    spent = (
        await session.execute(
            update(WalletRow)
            .where(_matching([wallet]), WalletRow.balance >= amount)
            .values(balance=WalletRow.balance - amount)
            .returning(WalletRow.id, WalletRow.balance)
        )
    ).first()
    if spent is None:
        return None
    wallet_id, balance = spent
    entry = {"wallet_id": wallet_id, "delta": -amount, "balance": balance, "reason": reason}
    await session.execute(insert(WalletEntryRow).values(entry))
    return int(balance)


def _key(wallet: Wallet) -> tuple[str, str, int, int | None]:
    return (wallet.game, wallet.currency, wallet.account_id, wallet.persona_id)


def _matching(wallets: Sequence[Wallet]) -> ColumnElement[bool]:
    return or_(
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


async def _held(session: AsyncSession, wallets: Sequence[Wallet]) -> list[int]:
    held = await session.execute(
        select(
            WalletRow.game,
            WalletRow.currency,
            WalletRow.account_id,
            WalletRow.persona_id,
            WalletRow.balance,
        ).where(_matching(wallets))
    )
    found = {(row[0], row[1], row[2], row[3]): row[4] for row in held}
    return [found.get(_key(wallet), wallet.allowance) for wallet in wallets]
