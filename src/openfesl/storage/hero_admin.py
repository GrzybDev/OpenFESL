from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.entitlements import ACTIVE
from openfesl.core.hero_admin import HeroHistory, ItemSource, TooManyUses
from openfesl.core.heroes import Hero, HeroRefused, Refusal
from openfesl.core.inventory import COUNTED, RENTED, Held, Ownership, fold
from openfesl.core.personas import Persona, PersonaState
from openfesl.core.wallets import MOST_BALANCE
from openfesl.storage.admin import action_row, audit_entry
from openfesl.storage.entitlements import REVOKED, add_item, locked_item
from openfesl.storage.heroes import set_default
from openfesl.storage.models import (
    AdminActionRow,
    ClawGrabRow,
    EntitlementRow,
    HeroAccountRow,
    MugshotRow,
    PersonaRow,
    PurchaseRow,
    RewardClaimRow,
)
from openfesl.storage.mugshots import SHOWN

# What each kind of purchase that a hero makes gave it the items through.
_PURCHASES = {
    "product": ItemSource.STORE,
    "bundle": ItemSource.HOT_DEAL,
    "box": ItemSource.BOX,
    "claw": ItemSource.CLAW,
}
# The admin's actions that a hero's history reads.
_RECORDED = [AdminAction.PERSONA_STATE, AdminAction.ITEM_GRANT, AdminAction.ITEM_REVOKE]


class SqlHeroAdmin:
    """What the admin panel reads and changes of heroes, in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def history(self, persona_id: int) -> HeroHistory:
        """Return what the records keep of a hero's items and mugshot.

        Args:
            persona_id: The hero's persona.
        """
        found: list[tuple[datetime, int, ItemSource]] = []
        async with self._sessions() as session:
            purchases = await session.execute(
                select(PurchaseRow.created_at, PurchaseRow.kind, PurchaseRow.outcome).where(
                    PurchaseRow.persona_id == persona_id
                )
            )
            for at, kind, outcome in purchases:
                found += [(at, item, _PURCHASES[kind]) for item in outcome["items"]]
            claims = await session.execute(
                select(RewardClaimRow.claimed_at, RewardClaimRow.outcome).where(
                    RewardClaimRow.persona_id == persona_id
                )
            )
            for at, outcome in claims:
                found += [(at, item, ItemSource.REWARD) for item, _ in outcome.get("items", [])]
            grabs = await session.execute(
                select(ClawGrabRow.created_at, ClawGrabRow.item_id).where(
                    ClawGrabRow.persona_id == persona_id
                )
            )
            found += [(at, item, ItemSource.CLAW) for at, item in grabs]
            mugshot = await session.scalar(
                select(MugshotRow.updated_at).where(MugshotRow.persona_id == persona_id, SHOWN)
            )
            actions = await session.scalars(
                select(AdminActionRow)
                .where(
                    AdminActionRow.action.in_(_RECORDED),
                    AdminActionRow.target == str(persona_id),
                )
                .order_by(AdminActionRow.id)
            )
            state, revoked = None, {}
            for row in actions:
                if row.action == AdminAction.PERSONA_STATE:
                    state = audit_entry(row)
                elif row.action == AdminAction.ITEM_REVOKE:
                    revoked[row.details["item"]] = row.at
                else:
                    found.append((row.at, row.details["item"], ItemSource.ADMIN))
        sources = {item: source for _, item, source in sorted(found)}
        return HeroHistory(sources, revoked, mugshot, state)

    async def rename(self, persona: Persona, name: str, note: str, admin: Admin) -> None:
        """Give a hero a new name, free of charge.

        Args:
            persona: The hero's persona.
            name: The new name, which follows the game's rule.
            note: Why, as the admin wrote it.
            admin: The admin who renamed it.

        Raises:
            HeroRefused: When another persona took the name, also at the same time.
        """
        async with self._sessions.begin() as session:
            try:
                await session.execute(
                    update(PersonaRow).where(PersonaRow.id == persona.id).values(name=name)
                )
            except IntegrityError:
                raise HeroRefused(Refusal.TAKEN) from None
            details = {"from": persona.name, "to": name, "note": note}
            session.add(action_row(AdminAction.HERO_RENAME, str(persona.id), details, admin))

    async def set_state(
        self,
        persona: Persona,
        game: str,
        state: PersonaState,
        default: Hero | None,
        note: str,
        admin: Admin,
    ) -> None:
        """Take a hero out of play or let it play again.

        Args:
            persona: The hero's persona.
            game: The game's data-file name.
            state: Its new state.
            default: The hero that becomes the account's default instead, if one does.
            note: Why, as the admin wrote it.
            admin: The admin who changed it.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(PersonaRow)
                .where(PersonaRow.id == persona.id)
                .values(state=state.value, state_changed_at=func.now())
            )
            details: dict[str, Any] = {"state": state.value, "note": note}
            if default is not None:
                await set_default(session, persona.account_id, game, default.persona.id)
                details["default"] = default.persona.name
            session.add(action_row(AdminAction.PERSONA_STATE, str(persona.id), details, admin))

    async def make_default(
        self, persona: Persona, game: str, was: Hero | None, note: str, admin: Admin
    ) -> None:
        """Make a hero its account's default hero, which the game is started as.

        Args:
            persona: The hero's persona.
            game: The game's data-file name.
            was: The default hero before, if any.
            note: Why, as the admin wrote it.
            admin: The admin who chose it.
        """
        async with self._sessions.begin() as session:
            await set_default(session, persona.account_id, game, persona.id)
            details = {"was": was.persona.name if was else None, "note": note}
            session.add(action_row(AdminAction.HERO_DEFAULT, str(persona.id), details, admin))

    async def grant_item(
        self, persona: Persona, item_id: int, gained: Ownership, note: str, admin: Admin
    ) -> Held | None:
        """Grant a hero an item, as the game folds what it gains; one taken out of use returns.

        Args:
            persona: The hero's persona.
            item_id: The item's number.
            gained: How the hero gains it.
            note: Why, as the admin wrote it.
            admin: The admin who granted it.

        Returns:
            How the hero holds the item then, or None when it owns the item for good already.

        Raises:
            TooManyUses: When the hero would then hold more uses than the game reads, also
                after a grant at the same time; nothing is granted.
        """
        async with self._sessions.begin() as session:
            row = await locked_item(session, persona.id, item_id)
            if row is not None and row.status != ACTIVE:
                held = fold(None, gained, datetime.now(UTC))
                assert held is not None
                row.status, row.tag = ACTIVE, f"{item_id}-{held.type}"
                row.use_count, row.expires_at = held.uses, held.expires
            else:
                held = await add_item(session, persona.account_id, persona.id, item_id, gained)
            if held is None:
                return None
            if (held.uses or 0) > MOST_BALANCE:
                # Leaving the block by the exception rolls the folded uses back.
                raise TooManyUses
            details = {"item": item_id, "ownership": gained.written, "note": note}
            details |= _left(held.expires, held.uses)
            session.add(action_row(AdminAction.ITEM_GRANT, str(persona.id), details, admin))
            return held

    async def change_item(
        self,
        persona: Persona,
        item_id: int,
        expires: datetime | None,
        uses: int | None,
        note: str,
        admin: Admin,
    ) -> bool:
        """Change when a hero's rental of an item ends, or how many uses it has left of one.

        Args:
            persona: The hero's persona.
            item_id: The item's number.
            expires: The rental's new end, for an item the hero rents.
            uses: The new number of uses left, for an item counted in uses.
            note: Why, as the admin wrote it.
            admin: The admin who changed it.

        Returns:
            Whether the hero holds the item in use that way.
        """
        async with self._sessions.begin() as session:
            row = await locked_item(session, persona.id, item_id)
            if row is None or row.status != ACTIVE:
                return False
            before = _left(row.expires_at, row.use_count)
            held = row.tag.partition("-")[2]
            if held == COUNTED and uses is not None:
                row.use_count = uses
            elif held == RENTED and expires is not None:
                row.expires_at = expires
            else:
                return False
            details = {"item": item_id, "before": before, "note": note}
            details |= _left(row.expires_at, row.use_count)
            session.add(action_row(AdminAction.ITEM_CHANGE, str(persona.id), details, admin))
            return True

    async def revoke_item(self, persona: Persona, item_id: int, note: str, admin: Admin) -> bool:
        """Take an item out of a hero's use, refunding nothing.

        Args:
            persona: The hero's persona.
            item_id: The item's number.
            note: Why, as the admin wrote it.
            admin: The admin who took it.

        Returns:
            Whether the hero held it in use.
        """
        async with self._sessions.begin() as session:
            row = await locked_item(session, persona.id, item_id)
            if row is None or row.status != ACTIVE:
                return False
            row.status = REVOKED
            details = {"item": item_id, "note": note}
            session.add(action_row(AdminAction.ITEM_REVOKE, str(persona.id), details, admin))
            return True

    async def grant_slots(
        self, account_id: int, game: str, count: int, note: str, admin: Admin
    ) -> int:
        """Grant an account further hero slots of a game, free; they stay with the account.

        Args:
            account_id: The account.
            game: The game's data-file name.
            count: How many.
            note: Why, as the admin wrote it.
            admin: The admin who granted them.

        Returns:
            The slots the admin granted the account in all.
        """
        async with self._sessions.begin() as session:
            statement = insert(HeroAccountRow).values(
                account_id=account_id, game=game, granted_slots=count
            )
            granted = await session.scalar(
                statement.on_conflict_do_update(
                    index_elements=[HeroAccountRow.account_id, HeroAccountRow.game],
                    set_={"granted_slots": HeroAccountRow.granted_slots + count},
                ).returning(HeroAccountRow.granted_slots)
            )
            assert isinstance(granted, int)
            details = {"game": game, "count": count, "granted": granted, "note": note}
            session.add(action_row(AdminAction.HERO_SLOTS, str(account_id), details, admin))
            return granted

    async def count(
        self,
        owner: Persona | int,
        game: str,
        tag: str,
        change: int,
        note: str,
        admin: Admin,
    ) -> tuple[int, bool]:
        """Add to or take from the uses a hero or an account holds of a tag, such as its chests.

        Nothing is taken that is not held; a take of more changes nothing.

        Args:
            owner: The hero's persona, or the number of the account that holds the tag itself.
            game: The game's data-file name.
            tag: The tag, such as that of a hero's treasure chests or an account's keys.
            change: How many uses to add, below 0 to take.
            note: Why, as the admin wrote it.
            admin: The admin who changed them.

        Returns:
            The uses held before, and whether they changed.
        """
        hero = isinstance(owner, Persona)
        account = owner.account_id if isinstance(owner, Persona) else owner
        persona = owner.id if isinstance(owner, Persona) else None
        async with self._sessions.begin() as session:
            # The row is made first, so that changes at once wait for each other on its lock.
            await session.execute(
                insert(EntitlementRow)
                .values(account_id=account, persona_id=persona, tag=tag, use_count=0)
                .on_conflict_do_nothing()
            )
            row = await session.scalar(
                select(EntitlementRow)
                .where(
                    EntitlementRow.account_id == account,
                    EntitlementRow.persona_id.is_not_distinct_from(persona),
                    EntitlementRow.tag == tag,
                )
                .with_for_update()
            )
            assert row is not None
            before = row.use_count or 0
            if before + change < 0:
                return before, False
            row.use_count = before + change
            details = {"game": game, "count": change, "before": before, "note": note}
            action = AdminAction.CHESTS if hero else AdminAction.CHEST_KEYS
            target = str(persona if hero else account)
            session.add(action_row(action, target, details, admin))
            return before, True

    async def remove_mugshot(self, persona: Persona, note: str, admin: Admin) -> bool:
        """Remove the mugshot that the game sent of a hero, until it sends another.

        Args:
            persona: The hero's persona.
            note: Why, as the admin wrote it.
            admin: The admin who removed it.

        Returns:
            Whether the hero had one.
        """
        async with self._sessions.begin() as session:
            removed = await session.scalar(
                update(MugshotRow)
                .where(MugshotRow.persona_id == persona.id, SHOWN)
                .values(image=b"")
                .returning(MugshotRow.persona_id)
            )
            if removed is not None:
                target = str(persona.id)
                session.add(action_row(AdminAction.MUGSHOT_REMOVE, target, {"note": note}, admin))
            return removed is not None


def _left(expires: datetime | None, uses: int | None) -> dict[str, Any]:
    # What a hero has of an item, as the log keeps it.
    return {"expires": expires.isoformat() if expires else None, "uses": uses}
