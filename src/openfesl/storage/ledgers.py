from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import Row, Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.wallets import Ledger, LedgerEntry, Wallet, WalletHead
from openfesl.storage.models import PurchaseRow, WalletEntryRow, WalletRow
from openfesl.storage.purchases import PURCHASE
from openfesl.storage.wallets import matching

# A purchase and the change of its wallet that paid it are written in one transaction, whose
# start both take as their time, so the time ties a purchase to its ledger entry.
_PAID = and_(
    WalletEntryRow.reason == PURCHASE,
    PurchaseRow.account_id == WalletRow.account_id,
    PurchaseRow.currency == WalletRow.currency,
    PurchaseRow.created_at == WalletEntryRow.created_at,
)


class SqlLedgerStore:
    """The wallets' ledgers in PostgreSQL, as the admin panel reads them."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def ledger(
        self, wallet: Wallet, reasons: Collection[str], offset: int, limit: int
    ) -> Ledger:
        """Return a page of a wallet's ledger, newest first, without opening the wallet.

        Args:
            wallet: The wallet.
            reasons: The reasons whose changes are listed; every one when empty.
            offset: How many changes to skip.
            limit: The most changes to return.
        """
        chosen = [WalletEntryRow.reason.in_(list(reasons))] if reasons else []
        async with self._sessions() as session:
            counted = await session.execute(
                select(WalletEntryRow.reason, func.count())
                .join(WalletRow, WalletRow.id == WalletEntryRow.wallet_id)
                .where(matching([wallet]))
                .group_by(WalletEntryRow.reason)
            )
            counts = {reason: number for reason, number in counted}
            rows = await session.execute(
                _entries()
                .where(matching([wallet]), *chosen)
                .order_by(WalletEntryRow.id.desc())
                .offset(offset)
                .limit(limit)
            )
            entries = [_entry(row) for row in rows]
        total = sum(n for reason, n in counts.items() if not reasons or reason in reasons)
        return Ledger(total, counts, entries)

    async def heads(self, wallets: Sequence[Wallet]) -> list[WalletHead]:
        """Return whether each wallet was opened, with its latest change, opening none.

        Args:
            wallets: The wallets.
        """
        heads = []
        async with self._sessions() as session:
            for wallet in wallets:
                opened = await session.scalar(select(WalletRow.id).where(matching([wallet])))
                row = (
                    await session.execute(
                        _entries()
                        .where(WalletRow.id == opened)
                        .order_by(WalletEntryRow.id.desc())
                        .limit(1)
                    )
                ).first()
                heads.append(WalletHead(opened is not None, _entry(row) if row else None))
        return heads


def _entries() -> Select[WalletEntryRow, int | None, dict[str, Any] | None]:
    return (
        select(WalletEntryRow, PurchaseRow.id, PurchaseRow.outcome)
        .join(WalletRow, WalletRow.id == WalletEntryRow.wallet_id)
        .outerjoin(PurchaseRow, _PAID)
    )


def _entry(row: Row[WalletEntryRow, int | None, dict[str, Any] | None]) -> LedgerEntry:
    entry, purchase, outcome = row
    return LedgerEntry(
        id=entry.id,
        at=entry.created_at,
        change=entry.delta,
        balance=entry.balance,
        reason=entry.reason,
        purchase=purchase,
        items=tuple(outcome["items"]) if outcome else (),
    )
