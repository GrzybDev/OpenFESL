from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.games import Credits
from openfesl.core.wallets import MOST_BALANCE, Wallet
from openfesl.storage.models import WalletEntryRow, WalletRow

# The ledger's reasons for the amount that a wallet opens with, for a dedicated server's
# credit, for what a refund gave back, and for an admin's grant or take.
ALLOWANCE = "allowance"
SERVER = "server"
REFUND = "refund"
ADMIN = "admin"


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

        A new wallet counts the allowance it opens with; what refunds gave back was received
        before and is not counted again.

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
                    and_(
                        WalletEntryRow.wallet_id == WalletRow.id,
                        WalletEntryRow.delta > 0,
                        WalletEntryRow.reason != REFUND,
                    ),
                )
                .where(matching(wallets))
                .group_by(WalletRow.id)
            )
            found = {(row[0], row[1], row[2], row[3]): int(row[4]) for row in rows}
        return [found.get(_key(wallet), wallet.allowance) for wallet in wallets]

    async def opened(self, wallet: Wallet) -> datetime | None:
        """Return when a wallet received its allowance, if its ledger records one.

        Args:
            wallet: The wallet.
        """
        async with self._sessions() as session:
            return await session.scalar(
                select(WalletEntryRow.created_at)
                .join(WalletRow, WalletRow.id == WalletEntryRow.wallet_id)
                .where(matching([wallet]), WalletEntryRow.reason == ALLOWANCE)
            )

    async def credit_from_server(
        self, wallet: Wallet, amount: int, transaction: str, credits: Credits
    ) -> int | None:
        """Credit a wallet with what a dedicated server sends, up to the most its window allows.

        Args:
            wallet: The wallet.
            amount: The amount the server sends.
            transaction: The id the server gave the credit.
            credits: What servers may credit to the wallet within a window.

        Returns:
            The balance, or None when the ledger holds the transaction id already.
        """
        async with self._sessions.begin() as session:
            wallet_id, _ = await locked(session, wallet)
            credited = await session.scalar(
                select(func.coalesce(func.sum(WalletEntryRow.delta), 0)).where(
                    WalletEntryRow.wallet_id == wallet_id,
                    WalletEntryRow.reason == SERVER,
                    WalletEntryRow.created_at > func.now() - timedelta(seconds=credits.within),
                )
            )
            allowed = min(amount, credits.most - int(credited or 0))
            return await credit(session, wallet, allowed, SERVER, transaction)


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
            .where(matching([wallet]), WalletRow.balance >= amount)
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


async def credit(
    session: AsyncSession, wallet: Wallet, amount: int, reason: str, transaction: str | None = None
) -> int | None:
    """Add an amount to a wallet, opening it first, and record it in its ledger.

    The wallet is locked while it changes, and its balance never passes the most a game holds:
    the ledger records what was added. A transaction id that the ledger holds already, also one
    that another credit commits meanwhile, adds nothing.

    Args:
        session: The transaction to credit in.
        wallet: The wallet.
        amount: The amount; one below zero adds nothing.
        reason: Why, as the ledger keeps it.
        transaction: The id the credit's sender gave it, which the ledger keeps once; none for
            a credit without one.

    Returns:
        The balance, or None when the ledger holds the transaction id already.
    """
    wallet_id, balance = await locked(session, wallet)
    added = max(0, min(amount, MOST_BALANCE - balance))
    entry = {
        "wallet_id": wallet_id,
        "delta": added,
        "balance": balance + added,
        "reason": reason,
        "transaction": transaction,
    }
    recorded = await session.scalar(
        insert(WalletEntryRow)
        .values(entry)
        .on_conflict_do_nothing(
            index_elements=[WalletEntryRow.transaction],
            index_where=WalletEntryRow.transaction.is_not(None),
        )
        .returning(WalletEntryRow.id)
    )
    if recorded is None:
        return None
    if added:
        await session.execute(
            update(WalletRow).where(WalletRow.id == wallet_id).values(balance=balance + added)
        )
    return balance + added


async def grant_up_to(session: AsyncSession, wallet: Wallet, total: int, reason: str) -> None:
    """Credit a wallet with what a reason has yet to grant it of a total, taking none back.

    The wallet is opened first and locked while its ledger is read, so that grants at the same
    time credit the total once.

    Args:
        session: The transaction to credit in.
        wallet: The wallet.
        total: What the reason grants the wallet in all.
        reason: Why, as the ledger keeps it.
    """
    wallet_id, _ = await locked(session, wallet)
    granted = await session.scalar(
        select(func.coalesce(func.sum(WalletEntryRow.delta), 0)).where(
            WalletEntryRow.wallet_id == wallet_id, WalletEntryRow.reason == reason
        )
    )
    missing = total - int(granted or 0)
    if missing > 0:
        await credit(session, wallet, missing, reason)


async def locked(session: AsyncSession, wallet: Wallet) -> tuple[int, int]:
    """Open a wallet if it is new and lock it until the transaction ends.

    Args:
        session: The transaction.
        wallet: The wallet.

    Returns:
        The wallet's row number and balance.
    """
    # Opened first, so that there is a row to lock.
    await open_wallets(session, [wallet])
    locked = await session.execute(
        select(WalletRow.id, WalletRow.balance).where(matching([wallet])).with_for_update()
    )
    wallet_id, balance = locked.one()
    return wallet_id, balance


def _key(wallet: Wallet) -> tuple[str, str, int, int | None]:
    return (wallet.game, wallet.currency, wallet.account_id, wallet.persona_id)


def matching(wallets: Sequence[Wallet]) -> ColumnElement[bool]:
    """Return the condition that picks the rows of some wallets.

    Args:
        wallets: The wallets.
    """
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
        ).where(matching(wallets))
    )
    found = {(row[0], row[1], row[2], row[3]): row[4] for row in held}
    return [found.get(_key(wallet), wallet.allowance) for wallet in wallets]
