import re
from dataclasses import dataclass

# How Magma tags say a hero owns an item, after the item's number: for good, for a number of
# days or for a number of uses (BFHeroes.exe 1.89 0x006a658b).
PERMANENT = "unlm"
RENTED = "tlmd"
COUNTED = "lmct"

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
