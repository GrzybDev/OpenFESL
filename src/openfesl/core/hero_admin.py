from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from openfesl.core.admin import AuditEntry
from openfesl.core.admins import Admin
from openfesl.core.heroes import Hero, Roster
from openfesl.core.inventory import Held, Ownership
from openfesl.core.personas import Persona, PersonaState


class TooManyUses(Exception):
    """Raised for an item grant after which a hero would hold more uses than the game reads."""


class ItemSource(StrEnum):
    """Where a hero's item came from: starting kit, store, hot deal, box, reward, Claw or admin."""

    STARTING_KIT = "starting_kit"
    STORE = "store"
    HOT_DEAL = "hot_deal"
    BOX = "box"
    REWARD = "reward"
    CLAW = "claw"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class HeroHistory:
    """What the records keep of a hero's items and mugshot.

    Attributes:
        sources: Where each item came from last, by number, where a purchase, a reward claim, a
            grab of the Claw or an admin's grant records it.
        revoked: When an admin last revoked each item, by number.
        mugshot: When its game sent the mugshot it has, if it has one.
        state: The admin's last change of its state, if any.
    """

    sources: Mapping[int, ItemSource]
    revoked: Mapping[int, datetime]
    mugshot: datetime | None
    state: AuditEntry | None = None


class HeroAdminStore(Protocol):
    """Where the admin panel reads what is recorded of heroes and changes them.

    Each change is recorded among the admin's actions with the admin's note, together with it.
    """

    async def history(self, persona_id: int) -> HeroHistory:
        """Return what the records keep of a hero's items and mugshot.

        Args:
            persona_id: The hero's persona.
        """

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

    async def remove_mugshot(self, persona: Persona, note: str, admin: Admin) -> bool:
        """Remove the mugshot that the game sent of a hero, until it sends another.

        Args:
            persona: The hero's persona.
            note: Why, as the admin wrote it.
            admin: The admin who removed it.

        Returns:
            Whether the hero had one.
        """


def next_default(roster: Roster, hero: Hero) -> Hero | None:
    """Return the hero that becomes an account's default when its default hero is disabled.

    It is the oldest of the others that the game can play; none when no other can.

    Args:
        roster: The account's heroes.
        hero: The default hero, which is disabled.
    """
    return next((other for other in roster.heroes if other != hero and other.playable), None)
