from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.gift_codes import (
    CodeChange,
    CodeGrant,
    CodeRefusal,
    CodeRefused,
    CodeTerms,
    GiftCode,
    GivenItem,
    Made,
    Redemption,
)
from openfesl.core.inventory import Held, Ownership
from openfesl.core.reward_rules import Gift
from openfesl.core.wallets import MOST_BALANCE
from openfesl.storage.admin import action_row
from openfesl.storage.entitlements import add_account_item, add_item
from openfesl.storage.models import (
    AccountRow,
    AdminActionRow,
    PersonaRow,
    ProductKeyRow,
    ProductKeyUseRow,
)
from openfesl.storage.wallets import credit, locked

# The ledger's reason for the Funds that codes give.
CODE = "code"


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

    async def gift_code(self, code: str, whole: bool = True) -> GiftCode | None:
        """Return a code, or the batch it belongs to, whether it gives anything or not.

        Args:
            code: The code, in any case.
            whole: Whether a code of a batch stands for the whole batch; otherwise for itself.
        """
        async with self._sessions() as session:
            row = await session.scalar(select(ProductKeyRow).where(_named(code)))
            if row is None:
                return None
            rows = [row]
            if row.batch is not None and whole:
                rows = list(
                    await session.scalars(
                        select(ProductKeyRow)
                        .where(ProductKeyRow.batch == row.batch)
                        .order_by(ProductKeyRow.id)
                    )
                )
            return _entry(rows, await _uses(session, [found.id for found in rows]))

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

    async def redemption(self, code: str, account_id: int) -> Redemption | None:
        """Return an account's redemption of a code, if it redeemed it.

        Args:
            code: The code as stored.
            account_id: The account.
        """
        async with self._sessions() as session:
            found = await _redemptions(
                session, _named(code), ProductKeyUseRow.account_id == account_id
            )
            return found[0] if found else None

    async def redemptions(self, entry: GiftCode) -> list[Redemption]:
        """Return the redemptions of a code or of a batch's codes, the newest first.

        Args:
            entry: The code or batch.
        """
        async with self._sessions() as session:
            return await _redemptions(session, _of(entry))

    async def redeemed_by(self, game: str, account_id: int) -> list[Redemption]:
        """Return the codes of a game that an account redeemed, the newest first.

        Args:
            game: The game's id.
            account_id: The account.
        """
        async with self._sessions() as session:
            return await _redemptions(
                session, ProductKeyRow.game == game, ProductKeyUseRow.account_id == account_id
            )

    async def redeem(self, grant: CodeGrant) -> Redemption:
        """Redeem a code for an account and give what it gives, all of it or none.

        The code is checked again under its lock, so that accounts redeeming it at once count
        its uses in turn and an account redeems it once.

        Args:
            grant: What the redemption gives.

        Raises:
            CodeRefused: When the code is unknown or disabled, ended or used up by now, the
                account redeemed it already, also at the same time, or its Funds would pass the
                most a wallet holds.
        """
        account = grant.account_id
        async with self._sessions.begin() as session:
            key = await session.scalar(
                select(ProductKeyRow).where(_named(grant.code)).with_for_update()
            )
            if key is None or key.game != grant.game or not key.enabled:
                raise CodeRefused(CodeRefusal.UNKNOWN)
            if key.expires_at is not None and key.expires_at <= datetime.now(UTC):
                raise CodeRefused(CodeRefusal.EXPIRED)
            this_redemption = (ProductKeyRow.id == key.id, ProductKeyUseRow.account_id == account)
            if await _redemptions(session, *this_redemption):
                raise CodeRefused(CodeRefusal.REDEEMED)
            if key.max_uses is not None and (await _uses(session, [key.id])).get(key.id, 0) >= (
                key.max_uses
            ):
                raise CodeRefused(CodeRefusal.USED_UP)
            # The account's keys, the hero's items, then the wallet, in the order purchases and
            # chest openings lock them, so that one at the same time waits rather than deadlocks.
            keys = None
            if grant.keys:
                keys = grant.keys[1], await add_account_item(session, account, *grant.keys)
            items = await _give(session, grant) if grant.hero_id is not None else []
            funds = None
            if grant.funds:
                wallet, amount = grant.funds
                _, balance = await locked(session, wallet)
                if balance + amount > MOST_BALANCE:
                    raise CodeRefused(CodeRefusal.BALANCE)
                after = await credit(session, wallet, amount, CODE)
                assert after is not None
                funds = amount, after
            outcome = {
                "items": [_written(given) for given in items],
                "funds": list(funds) if funds else None,
                "keys": list(keys) if keys else None,
            }
            used = ProductKeyUseRow(
                key_id=key.id, account_id=account, persona_id=grant.hero_id, outcome=outcome
            )
            session.add(used)
            await session.flush()
            [redeemed] = await _redemptions(session, *this_redemption)
            return redeemed

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
        sum(uses.get(row.id, 0) for row in rows),
        tuple(row.code for row in rows),
        first.batch is not None,
    )


def _named(code: str) -> ColumnElement[bool]:
    # A code that gives a gift, which its own form writes in capitals.
    return ProductKeyRow.gift.is_not(None) & (func.upper(ProductKeyRow.code) == code.upper())


async def _give(session: AsyncSession, grant: CodeGrant) -> list[GivenItem]:
    assert grant.hero_id is not None
    given = []
    # In the items' order, as purchases and rewards lock them, so that all wait in turn.
    for item_id, ownership in sorted(grant.items, key=lambda item: item[0]):
        held = await add_item(session, grant.account_id, grant.hero_id, item_id, ownership)
        if held is None:
            fallback, owned = grant.fallback
            after = await add_item(session, grant.account_id, grant.hero_id, fallback, owned)
            given.append(GivenItem(fallback, owned, after, True))
        else:
            given.append(GivenItem(item_id, ownership, held))
    return given


def _written(given: GivenItem) -> dict[str, Any]:
    # An item a redemption gave, as its outcome keeps it.
    held = given.held
    kept = (
        [held.type, held.expires.isoformat() if held.expires else None, held.uses] if held else None
    )
    return {
        "item": given.item_id,
        "given": given.ownership.written,
        "held": kept,
        "fallback": given.fallback,
    }


async def _redemptions(session: AsyncSession, *conditions: ColumnElement[bool]) -> list[Redemption]:
    rows = await session.execute(
        select(
            ProductKeyUseRow,
            ProductKeyRow.code,
            ProductKeyRow.labels,
            AccountRow.nuid,
            PersonaRow.name,
        )
        .join(ProductKeyRow, ProductKeyRow.id == ProductKeyUseRow.key_id)
        .join(AccountRow, AccountRow.id == ProductKeyUseRow.account_id)
        .outerjoin(PersonaRow, PersonaRow.id == ProductKeyUseRow.persona_id)
        .where(ProductKeyRow.gift.is_not(None), *conditions)
        .order_by(ProductKeyUseRow.used_at.desc(), ProductKeyUseRow.key_id.desc())
    )
    found = []
    for used, code, labels, account, hero in rows:
        outcome = used.outcome or {}
        items = []
        for item in outcome.get("items", []):
            ownership = Ownership.read(item["given"])
            kept = item["held"]
            held = (
                Held(kept[0], datetime.fromisoformat(kept[1]) if kept[1] else None, kept[2])
                if kept
                else None
            )
            items.append(GivenItem(item["item"], ownership, held, item["fallback"]))
        funds, keys = outcome.get("funds"), outcome.get("keys")
        found.append(
            Redemption(
                code,
                labels,
                used.account_id,
                account,
                hero,
                used.used_at,
                tuple(items),
                (funds[0], funds[1]) if funds else None,
                (keys[0], keys[1]) if keys else None,
            )
        )
    return found
