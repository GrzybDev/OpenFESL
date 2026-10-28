import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

# How Magma tags say a hero owns an item, after the item's number: for good, for a number of
# days or for a number of uses (BFHeroes.exe 1.89 0x006a658b).
PERMANENT = "unlm"
RENTED = "tlmd"
COUNTED = "lmct"
# Every type a tag may name, in the order in which a game prefers them, any other one first
# (0x007baaa6).
_PREFERRED = (PERMANENT, "tlmg", "tlmp", RENTED, "lmrd", COUNTED)

# An ownership as game data writes it: perm, days such as 7d, or uses such as x75.
_WRITTEN = re.compile(r"perm|(?P<days>[1-9][0-9]*)d|x(?P<uses>[1-9][0-9]*)")


@dataclass(frozen=True, slots=True)
class Ownership:
    """How a hero owns an item: for good, for a number of days or for a number of uses.

    Attributes:
        type: The type its Magma tag names after the item's number.
        days: How long a rental lasts.
        uses: How many uses an item limited by them brings.
    """

    type: str
    days: int | None = None
    uses: int | None = None

    @classmethod
    def read(cls, text: object) -> object:
        """Read an ownership as game data writes it, leaving anything else to its validation.

        Args:
            text: perm, a number of days such as 7d, or of uses such as x75.

        Raises:
            ValueError: When the text is none of them.
        """
        if not isinstance(text, str):
            return text
        written = _WRITTEN.fullmatch(text)
        if written is None:
            raise ValueError(f"{text!r} is no ownership such as perm, 7d or x75")
        if days := written["days"]:
            return cls(RENTED, days=int(days))
        if uses := written["uses"]:
            return cls(COUNTED, uses=int(uses))
        return cls(PERMANENT)

    def tag(self, item_id: int) -> str:
        """Return the Magma tag of an item owned this way.

        Args:
            item_id: The item's number.
        """
        return f"{item_id}-{self.type}"


@dataclass(frozen=True, slots=True)
class Held:
    """How a hero holds an item: the type of its tag, until when and with how many uses.

    Attributes:
        type: The type its Magma tag names after the item's number.
        expires: When a rental ends.
        uses: The uses left of an item limited by them.
    """

    type: str
    expires: datetime | None = None
    uses: int | None = None


def fold(held: Held | None, gained: Ownership, now: datetime) -> Held | None:
    """Return how a hero holds an item once it gains it, as the game folds what it buys.

    A type the game prefers replaces the one held; otherwise the held one stays, adding the
    uses gained and lengthening a rental from its end, or from now once it has ended
    (BFHeroes.exe 1.89 0x007baaa6).

    Args:
        held: How the hero holds the item; none when it does not.
        gained: How the hero gains it.
        now: The time it gains it.

    Returns:
        How the hero holds it then, or None when it owns the item for good, which gains it
        nothing.
    """
    days = timedelta(days=gained.days or 0)
    if held is None or _rank(gained.type) < _rank(held.type):
        return Held(gained.type, now + days if gained.days else None, gained.uses)
    if held.type == PERMANENT:
        return None
    uses = (held.uses or 0) + gained.uses if gained.uses else held.uses
    expires = held.expires
    if gained.type == RENTED and held.type == RENTED:
        expires = max(held.expires or now, now) + days
    return Held(held.type, expires, uses)


def gains(item_id: int, gained: Ownership, owned: Collection[int]) -> bool:
    """Tell whether a hero gains anything from an item, given the items it owns for good.

    Uses add up whatever the hero owns, while an item owned for good gains nothing more.

    Args:
        item_id: The item's number.
        gained: How the hero would own it.
        owned: The numbers of the items the hero owns for good.
    """
    return gained.type == COUNTED or item_id not in owned


def _rank(type: str) -> int:
    return _PREFERRED.index(type) if type in _PREFERRED else 0
