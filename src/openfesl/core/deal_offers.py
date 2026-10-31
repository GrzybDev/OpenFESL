from datetime import datetime
from typing import Any, Protocol

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.deal_rules import DealRules
from openfesl.core.store import Bundle, ListedBundle

# The states of the admin's offers in the order the panel lists them.
STATES = ("live", "scheduled", "off", "ended")


def offer_state(bundle: Bundle, now: datetime, rules: DealRules) -> str:
    """Return whether an admin's offer is scheduled, live, ended or off at a moment.

    An offer that has not ended is off while its section is switched off, which hides it.

    Args:
        bundle: The offer's bundle.
        now: The moment.
        rules: How the game's hot deals change, whose sections list the offer.
    """
    section = rules.sections.get(bundle.section) if bundle.section else None
    if bundle.ends is not None and bundle.ends <= now:
        return "ended"
    if section is not None and not section.enabled:
        return "off"
    if bundle.starts is not None and bundle.starts > now:
        return "scheduled"
    return "live"


class OfferStore(Protocol):
    """Where the admin's own offers among the hot deals are kept."""

    async def offers(self, game: str, since: datetime) -> list[ListedBundle]:
        """Return a game's admin offers that end after a moment or never, the newest first.

        Args:
            game: The game's data-file name.
            since: The moment.
        """

    async def offer(self, game: str, number: int) -> ListedBundle | None:
        """Return an admin offer of a game by its bundle number, whether it is sold now or not.

        Args:
            game: The game's data-file name.
            number: The offer's bundle number.
        """

    async def save(
        self,
        game: str,
        number: int | None,
        bundle: Bundle,
        action: AdminAction,
        details: dict[str, Any],
        admin: Admin,
    ) -> int | None:
        """Add an admin offer or change one, with its products, and record it, all or nothing.

        Args:
            game: The game's data-file name.
            number: The offer's bundle number; none for a new offer.
            bundle: The offer as it is to be sold.
            action: What the admin did.
            details: What the admin's log keeps of it.
            admin: The admin who did it.

        Returns:
            The offer's bundle number; none for a number that is no admin offer of the game.
        """
