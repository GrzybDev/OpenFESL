import secrets
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PositiveInt, StringConstraints

# Upper-case letters and digits only, so that a code reads the same in any case.
type Alphabet = Annotated[str, StringConstraints(pattern=r"^[0-9A-Z]{2,}$")]


class CodeRules(BaseModel):
    """How a game's site writes the codes that give gifts, which accounts redeem on it.

    Attributes:
        alphabet: The characters a code is drawn from.
        groups: How many groups of characters a code has, joined by dashes.
        size: How many characters each group has.
    """

    model_config = ConfigDict(frozen=True)

    alphabet: Alphabet
    groups: PositiveInt
    size: PositiveInt

    def generate(self) -> str:
        """Return a new code drawn at random, such as 7KQ4-M2XC-9HTV-RB3E."""
        drawn = "".join(secrets.choice(self.alphabet) for _ in range(self.groups * self.size))
        return self._grouped(drawn)

    def read(self, typed: str) -> str | None:
        """Return a code as typed in the code's own form, ignoring capitals, spaces and dashes.

        Args:
            typed: The code as typed.

        Returns:
            The code grouped with dashes, or None for text that is no code in this form.
        """
        bare = "".join(typed.split()).replace("-", "").upper()
        if len(bare) != self.groups * self.size or not set(bare) <= set(self.alphabet):
            return None
        return self._grouped(bare)

    def _grouped(self, bare: str) -> str:
        return "-".join(bare[start : start + self.size] for start in range(0, len(bare), self.size))
