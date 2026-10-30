from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ItemSource(StrEnum):
    """Where a hero's item came from: starting kit, store, hot deal, box, reward or an admin."""

    STARTING_KIT = "starting_kit"
    STORE = "store"
    HOT_DEAL = "hot_deal"
    BOX = "box"
    REWARD = "reward"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class HeroHistory:
    """What the records keep of a hero's items and mugshot.

    Attributes:
        sources: Where each item came from last, by number, where a purchase, a reward claim or
            an admin's grant records it.
        revoked: When an admin last revoked each item, by number.
        mugshot: When its game sent the mugshot it has, if it has one.
    """

    sources: Mapping[int, ItemSource]
    revoked: Mapping[int, datetime]
    mugshot: datetime | None


class HeroAdminStore(Protocol):
    """Where the admin panel reads what is recorded of heroes and changes them."""

    async def history(self, persona_id: int) -> HeroHistory:
        """Return what the records keep of a hero's items and mugshot.

        Args:
            persona_id: The hero's persona.
        """
