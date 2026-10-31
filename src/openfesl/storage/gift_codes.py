from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.gift_codes import CodeChange, CodeTerms, GiftCode, Made
from openfesl.core.reward_rules import Gift
from openfesl.storage.admin import action_row
from openfesl.storage.models import AdminActionRow, ProductKeyRow, ProductKeyUseRow


class SqlGiftCodeStore:
    """The codes that give gifts on the games' sites, in PostgreSQL beside the product keys."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def gift_codes(self, game: str) -> list[GiftCode]:
        """Return a game's codes, the newest first.

        Args:
            game: The game's id.
        """
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(ProductKeyRow)
                    .where(ProductKeyRow.game == game, ProductKeyRow.gift.is_not(None))
                    .order_by(ProductKeyRow.id.desc())
                )
            )
            uses = await _uses(session, [row.id for row in rows])
        return [_entry(row, uses) for row in rows]

    async def gift_code(self, code: str) -> GiftCode | None:
        """Return a code, whether it gives anything or not.

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
            return _entry(row, await _uses(session, [row.id]))

    async def made(self, code: str) -> Made | None:
        """Return when a code was made, as the admin's actions record it.

        Args:
            code: The code, as stored.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(AdminActionRow).where(
                    AdminActionRow.action == AdminAction.KEY_ADD, AdminActionRow.target == code
                )
            )
            return Made(row.at, row.admin_name, row.details.get("note", "")) if row else None

    async def make(self, game: str, code: str, terms: CodeTerms, change: CodeChange) -> bool:
        """Make a code and record it among the admin's actions.

        Args:
            game: The game's id.
            code: The code, which is new.
            terms: What it gives and on what terms.
            change: Who makes it and why.

        Returns:
            Whether it was made, which it is not when a key has the code already.
        """
        gift = terms.gift.written
        async with self._sessions.begin() as session:
            made = await session.scalar(
                insert(ProductKeyRow)
                .values(
                    code=code,
                    game=game,
                    gift=gift,
                    labels=dict(terms.labels),
                    max_uses=terms.max_uses,
                    once_per_account=True,
                    expires_at=terms.expires,
                )
                .on_conflict_do_nothing()
                .returning(ProductKeyRow.id)
            )
            if made is None:
                return False
            details: dict[str, Any] = {
                "game": game,
                "gift": gift,
                "label": terms.labels.get("en", ""),
                "expires": terms.expires.isoformat() if terms.expires else None,
                "note": change.note,
                "starting": False,
                "max_uses": terms.max_uses,
            }
            session.add(action_row(AdminAction.KEY_ADD, code, details, change.admin))
        return True

    async def change(self, entry: GiftCode, terms: CodeTerms, change: CodeChange) -> None:
        """Change what a code gives from now on, and record it.

        Args:
            entry: The code.
            terms: What it gives and on what terms from now on.
            change: Who changes it and why, with what changed.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(ProductKeyRow)
                .where(ProductKeyRow.id == entry.id)
                .values(
                    gift=terms.gift.written,
                    labels=dict(terms.labels),
                    max_uses=terms.max_uses,
                    expires_at=terms.expires,
                )
            )
            details = {**change.details, "game": entry.game, "note": change.note}
            session.add(action_row(AdminAction.KEY_CHANGED, entry.code, details, change.admin))

    async def switch(self, entry: GiftCode, enabled: bool, change: CodeChange) -> bool:
        """Enable or disable a code, and record it.

        Args:
            entry: The code.
            enabled: Whether it may be redeemed from now on.
            change: Who switches it and why.

        Returns:
            Whether it changed, which it does not when it was so already.
        """
        async with self._sessions.begin() as session:
            changed = list(
                await session.scalars(
                    update(ProductKeyRow)
                    .where(ProductKeyRow.id == entry.id, ProductKeyRow.enabled != enabled)
                    .values(enabled=enabled)
                    .returning(ProductKeyRow.id)
                )
            )
            if changed:
                details = {"enabled": enabled, "game": entry.game, "note": change.note}
                session.add(action_row(AdminAction.KEY_STATE, entry.code, details, change.admin))
            return bool(changed)


async def _uses(session: AsyncSession, keys: Sequence[int]) -> dict[int, int]:
    counted = await session.execute(
        select(ProductKeyUseRow.key_id, func.count())
        .where(ProductKeyUseRow.key_id.in_(keys))
        .group_by(ProductKeyUseRow.key_id)
    )
    return {key_id: count for key_id, count in counted}


def _entry(row: ProductKeyRow, uses: dict[int, int]) -> GiftCode:
    assert row.game is not None and row.gift is not None
    return GiftCode(
        row.id,
        row.code,
        row.game,
        Gift.model_validate(row.gift),
        row.labels,
        row.max_uses,
        row.expires_at,
        row.enabled,
        row.created_at,
        uses.get(row.id, 0),
    )
