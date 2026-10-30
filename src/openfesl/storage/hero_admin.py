from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.hero_admin import HeroHistory, ItemSource
from openfesl.core.heroes import Hero, HeroRefused, Refusal
from openfesl.core.personas import Persona, PersonaState
from openfesl.storage.admin import action_row, audit_entry
from openfesl.storage.heroes import set_default
from openfesl.storage.models import (
    AdminActionRow,
    MugshotRow,
    PersonaRow,
    PurchaseRow,
    RewardClaimRow,
)
from openfesl.storage.mugshots import SHOWN

# What each kind of purchase that a hero makes gave it the items through.
_PURCHASES = {"product": ItemSource.STORE, "bundle": ItemSource.HOT_DEAL, "box": ItemSource.BOX}


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
            mugshot = await session.scalar(
                select(MugshotRow.updated_at).where(MugshotRow.persona_id == persona_id, SHOWN)
            )
            state = await session.scalar(
                select(AdminActionRow)
                .where(
                    AdminActionRow.action == AdminAction.PERSONA_STATE,
                    AdminActionRow.target == str(persona_id),
                )
                .order_by(AdminActionRow.id.desc())
                .limit(1)
            )
        sources = {item: source for _, item, source in sorted(found)}
        return HeroHistory(sources, {}, mugshot, audit_entry(state) if state else None)

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
