from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.gift_codes import CodeChange, CodeTerms, GiftCode, Made
from openfesl.core.reward_rules import Gift
from openfesl.storage.admin import action_row
from openfesl.storage.models import AdminActionRow, ProductKeyRow, ProductKeyUseRow


class CodeTaken(Exception):
    """Raised inside a transaction that makes codes when a key has one of them already."""


class SqlGiftCodeStore:
    """The codes that give gifts on the games' sites, in PostgreSQL beside the product keys."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def gift_codes(self, game: str) -> list[GiftCode]:
        """Return a game's codes, a batch as one, the newest first.

        Args:
            game: The game's id.
        """
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(ProductKeyRow)
                    .where(ProductKeyRow.game == game, ProductKeyRow.gift.is_not(None))
                    .order_by(ProductKeyRow.id)
                )
            )
            uses = await _uses(session, [row.id for row in rows])
        grouped: dict[int, list[ProductKeyRow]] = {}
        for row in rows:
            grouped.setdefault(row.batch or row.id, []).append(row)
        entries = [_entry(found, uses) for found in grouped.values()]
        return sorted(entries, key=lambda entry: entry.id, reverse=True)

    async def gift_code(self, code: str) -> GiftCode | None:
        """Return a code, or the batch it belongs to, whether it gives anything or not.

        Args:
            code: The code, in any case.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(ProductKeyRow).where(
                    ProductKeyRow.gift.is_not(None), func.upper(ProductKeyRow.code) == code.upper()
                )
            )
            if row is None:
                return None
            rows = [row]
            if row.batch is not None:
                rows = list(
                    await session.scalars(
                        select(ProductKeyRow)
                        .where(ProductKeyRow.batch == row.batch)
                        .order_by(ProductKeyRow.id)
                    )
                )
            return _entry(rows, await _uses(session, [found.id for found in rows]))

    async def batch_codes(self, batch: int) -> list[str]:
        """Return the codes of a batch, in the order they were made.

        Args:
            batch: The number of the batch's first key.
        """
        async with self._sessions() as session:
            codes = await session.scalars(
                select(ProductKeyRow.code)
                .where(ProductKeyRow.batch == batch)
                .order_by(ProductKeyRow.id)
            )
            return list(codes)

    async def made(self, code: str) -> Made | None:
        """Return when a code or batch was made, as the admin's actions record it.

        Args:
            code: The code, or a batch's first code, as stored.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(AdminActionRow).where(
                    AdminActionRow.action.in_([AdminAction.KEY_ADD, AdminAction.KEY_BATCH]),
                    AdminActionRow.target == code,
                )
            )
            return Made(row.at, row.admin_name, row.details.get("note", "")) if row else None

    async def make(
        self, game: str, codes: Sequence[str], terms: CodeTerms, change: CodeChange
    ) -> bool:
        """Make a code, or a batch of personal codes, and record it among the admin's actions.

        Args:
            game: The game's id.
            codes: The code, or the batch's codes, each new.
            terms: What each gives and on what terms; a batch's codes work for one account each.
            change: Who makes them and why.

        Returns:
            Whether they were made, which they are not when a key has one of the codes already.
        """
        batch = len(codes) > 1
        values = {
            "game": game,
            "gift": terms.gift.written,
            "labels": dict(terms.labels),
            "max_uses": 1 if batch else terms.max_uses,
            "once_per_account": True,
            "expires_at": terms.expires,
        }
        try:
            async with self._sessions.begin() as session:
                made = list(
                    await session.scalars(
                        insert(ProductKeyRow)
                        .values([values | {"code": code} for code in codes])
                        .on_conflict_do_nothing()
                        .returning(ProductKeyRow.id)
                    )
                )
                if len(made) != len(codes):
                    raise CodeTaken
                details: dict[str, Any] = {
                    "game": game,
                    "gift": values["gift"],
                    "label": terms.labels.get("en", ""),
                    "expires": terms.expires.isoformat() if terms.expires else None,
                    "note": change.note,
                }
                if batch:
                    await session.execute(
                        update(ProductKeyRow)
                        .where(ProductKeyRow.id.in_(made))
                        .values(batch=min(made))
                    )
                    details["count"] = len(codes)
                    action = action_row(AdminAction.KEY_BATCH, codes[0], details, change.admin)
                else:
                    details |= {"starting": False, "max_uses": terms.max_uses}
                    action = action_row(AdminAction.KEY_ADD, codes[0], details, change.admin)
                session.add(action)
        except CodeTaken:
            return False
        return True

    async def change(self, entry: GiftCode, terms: CodeTerms, change: CodeChange) -> None:
        """Change what a code or every code of a batch gives from now on, and record it.

        Args:
            entry: The code or the batch.
            terms: What it gives and on what terms from now on.
            change: Who changes it and why, with what changed.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(ProductKeyRow)
                .where(_of(entry))
                .values(
                    gift=terms.gift.written,
                    labels=dict(terms.labels),
                    max_uses=1 if entry.batch else terms.max_uses,
                    expires_at=terms.expires,
                )
            )
            details = {**change.details, "game": entry.game, "note": change.note}
            session.add(action_row(AdminAction.KEY_CHANGED, entry.code, details, change.admin))

    async def switch(self, entry: GiftCode, enabled: bool, change: CodeChange) -> bool:
        """Enable or disable a code or every code of a batch, and record it.

        Args:
            entry: The code or the batch.
            enabled: Whether it may be redeemed from now on.
            change: Who switches it and why.

        Returns:
            Whether it changed, which it does not when it was so already.
        """
        async with self._sessions.begin() as session:
            changed = list(
                await session.scalars(
                    update(ProductKeyRow)
                    .where(_of(entry), ProductKeyRow.enabled != enabled)
                    .values(enabled=enabled)
                    .returning(ProductKeyRow.id)
                )
            )
            if changed:
                details = {"enabled": enabled, "game": entry.game, "note": change.note}
                session.add(action_row(AdminAction.KEY_STATE, entry.code, details, change.admin))
            return bool(changed)


def _of(entry: GiftCode) -> ColumnElement[bool]:
    # The key of a code, or every key of a batch, which the batch's first key names.
    return or_(ProductKeyRow.id == entry.id, ProductKeyRow.batch == entry.id)


async def _uses(session: AsyncSession, keys: Sequence[int]) -> dict[int, int]:
    counted = await session.execute(
        select(ProductKeyUseRow.key_id, func.count())
        .where(ProductKeyUseRow.key_id.in_(keys))
        .group_by(ProductKeyUseRow.key_id)
    )
    return {key_id: count for key_id, count in counted}


def _entry(rows: list[ProductKeyRow], uses: dict[int, int]) -> GiftCode:
    # A code of its own, or a batch whose codes share everything but their code.
    first = rows[0]
    assert first.game is not None and first.gift is not None
    return GiftCode(
        first.id,
        first.code,
        first.game,
        Gift.model_validate(first.gift),
        first.labels,
        first.max_uses,
        first.expires_at,
        first.enabled,
        first.created_at,
        sum(uses.get(row.id, 0) for row in rows),
        len(rows),
        first.batch is not None,
    )
