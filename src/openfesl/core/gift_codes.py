from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from openfesl.core.admins import Admin
from openfesl.core.reward_rules import Gift

# The most personal codes one batch makes.
MOST_BATCH = 1000


class CodeState(StrEnum):
    """Whether a code gives its gift now, as the admin panel names it."""

    ENABLED = "enabled"
    USED_UP = "used_up"
    """As many accounts as it allows redeemed it."""
    EXPIRED = "expired"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class GiftCode:
    """A code of a game's site that gives a gift, or a batch of personal codes made alike.

    The codes of a batch share their gift, labels, end and state, and each works for one account.

    Attributes:
        id: The number of its key, or of the batch's first key, which names the batch.
        code: The code; the first code of a batch.
        game: The id of the game whose site redeems it.
        gift: What it gives: items to one hero, Funds and keys to the account.
        labels: What players see it called, by language.
        max_uses: How many accounts may redeem it; None for any number, one each for a batch's.
        expires: When it can no longer be redeemed; never when None.
        enabled: Whether it may be redeemed.
        created: When it was made.
        uses: How many accounts redeemed it, or a batch's codes.
        count: How many codes the batch has; 1 for a code of its own.
        batch: Whether it is a batch.
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
    count: int = 1
    batch: bool = False

    def state(self, now: datetime) -> CodeState:
        """Return whether it gives its gift at a moment, a disabled one above all.

        Args:
            now: The moment.
        """
        if not self.enabled:
            return CodeState.DISABLED
        if self.expires is not None and self.expires <= now:
            return CodeState.EXPIRED
        most = self.count if self.batch else self.max_uses
        return CodeState.USED_UP if most is not None and self.uses >= most else CodeState.ENABLED

    def label(self, language: str) -> str:
        """Return what players see it called in a language, else in English.

        Args:
            language: The language's code.
        """
        return self.labels.get(language) or self.labels.get("en", "")


@dataclass(frozen=True, slots=True)
class Made:
    """When a code or batch was made, by whom and why.

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
    """What a code gives and on what terms, as the admin makes or changes it.

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
    """Who changes a code and why, with what the audit log keeps of it.

    Attributes:
        admin: The admin.
        note: Why, as they wrote it.
        details: What changed, as the audit log keeps it besides the note.
    """

    admin: Admin
    note: str
    details: Mapping[str, Any] = field(default_factory=dict)


class GiftCodeStore(Protocol):
    """Where the codes that give gifts are kept."""

    async def gift_codes(self, game: str) -> list[GiftCode]:
        """Return a game's codes, a batch as one, the newest first.

        Args:
            game: The game's id.
        """

    async def gift_code(self, code: str) -> GiftCode | None:
        """Return a code, or the batch it belongs to, whether it gives anything or not.

        Args:
            code: The code, in any case.
        """

    async def batch_codes(self, batch: int) -> list[str]:
        """Return the codes of a batch, in the order they were made.

        Args:
            batch: The number of the batch's first key.
        """

    async def made(self, code: str) -> Made | None:
        """Return when a code or batch was made, as the admin's actions record it.

        Args:
            code: The code, or a batch's first code, as stored.
        """

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

    async def change(self, entry: GiftCode, terms: CodeTerms, change: CodeChange) -> None:
        """Change what a code or every code of a batch gives from now on, and record it.

        Args:
            entry: The code or the batch.
            terms: What it gives and on what terms from now on.
            change: Who changes it and why, with what changed.
        """

    async def switch(self, entry: GiftCode, enabled: bool, change: CodeChange) -> bool:
        """Enable or disable a code or every code of a batch, and record it.

        Args:
            entry: The code or the batch.
            enabled: Whether it may be redeemed from now on.
            change: Who switches it and why.

        Returns:
            Whether it changed, which it does not when it was so already.
        """
