from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from openfesl.core.admins import Admin
from openfesl.core.reward_rules import Gift


class CodeState(StrEnum):
    """Whether a code gives its gift now, as the admin panel names it."""

    ENABLED = "enabled"
    USED_UP = "used_up"
    """As many accounts as it allows redeemed it."""
    EXPIRED = "expired"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class GiftCode:
    """A code of a game's site that gives a gift.

    Attributes:
        id: The number of its key.
        code: The code.
        game: The id of the game whose site redeems it.
        gift: What it gives: items to one hero, Funds and keys to the account.
        labels: What players see it called, by language.
        max_uses: How many accounts may redeem it; None for any number.
        expires: When it can no longer be redeemed; never when None.
        enabled: Whether it may be redeemed.
        created: When it was made.
        uses: How many accounts redeemed it.
    """

    id: int
    code: str
    game: str
    gift: Gift
    labels: Mapping[str, str]
    max_uses: int | None
    expires: datetime | None
    enabled: bool
    created: datetime
    uses: int

    def state(self, now: datetime) -> CodeState:
        """Return whether it gives its gift at a moment, a disabled one above all.

        Args:
            now: The moment.
        """
        if not self.enabled:
            return CodeState.DISABLED
        if self.expires is not None and self.expires <= now:
            return CodeState.EXPIRED
        if self.max_uses is not None and self.uses >= self.max_uses:
            return CodeState.USED_UP
        return CodeState.ENABLED

    def label(self, language: str) -> str:
        """Return what players see it called in a language, else in English.

        Args:
            language: The language's code.
        """
        return self.labels.get(language) or self.labels.get("en", "")


@dataclass(frozen=True, slots=True)
class Made:
    """When a code was made, by whom and why.

    Attributes:
        at: When.
        by: The admin's name; none for the command line.
        note: Why, as the admin wrote it.
    """

    at: datetime
    by: str | None
    note: str


@dataclass(frozen=True, slots=True)
class CodeTerms:
    """What a code gives and on what terms, as the admin makes it.

    Attributes:
        gift: What it gives.
        labels: What players see it called, by language, English among them.
        max_uses: How many accounts may redeem it; None for any number.
        expires: When it can no longer be redeemed; never when None.
    """

    gift: Gift
    labels: Mapping[str, str]
    max_uses: int | None
    expires: datetime | None


@dataclass(frozen=True, slots=True)
class CodeChange:
    """Who makes a code and why.

    Attributes:
        admin: The admin.
        note: Why, as they wrote it.
    """

    admin: Admin
    note: str


class GiftCodeStore(Protocol):
    """Where the codes that give gifts are kept."""

    async def gift_codes(self, game: str) -> list[GiftCode]:
        """Return a game's codes, the newest first.

        Args:
            game: The game's id.
        """

    async def gift_code(self, code: str) -> GiftCode | None:
        """Return a code, whether it gives anything or not.

        Args:
            code: The code, in any case.
        """

    async def made(self, code: str) -> Made | None:
        """Return when a code was made, as the admin's actions record it.

        Args:
            code: The code, as stored.
        """

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
