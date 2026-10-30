from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.hero_admin import HeroHistory, ItemSource
from openfesl.storage.models import MugshotRow, PurchaseRow, RewardClaimRow

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
                select(MugshotRow.updated_at).where(MugshotRow.persona_id == persona_id)
            )
        sources = {item: source for _, item, source in sorted(found)}
        return HeroHistory(sources, {}, mugshot)
