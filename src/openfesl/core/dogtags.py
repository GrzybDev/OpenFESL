import binascii
import struct
from base64 import b64decode
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# The day from which the game counts the days of its dog tags.
EPOCH = datetime(2008, 1, 1, tzinfo=UTC)
# A tag's 28 bytes: the name, the day it was last taken, the gold, silver and bronze tags taken
# and the rank the player had then (ClientDogTags::encode in the game's code).
_TAG = struct.Struct("<16sfHHHBx")


@dataclass(frozen=True, slots=True)
class DogTag:
    """The dog tag of a player whose tags another player's game took.

    Attributes:
        persona_id: The player whose tag was taken.
        name: The player's name when the tag was last taken.
        rank: The player's rank then.
        taken: When the tag was last taken, by the game's clock.
        count: How often the tag was taken.
    """

    persona_id: int
    name: str
    rank: int
    taken: datetime
    count: int


def read_dog_tags(entries: Mapping[str, str]) -> list[DogTag]:
    """Return the dog tags of the record in which a player's game keeps them, newest first.

    The record holds one entry per player whose tag the game took, by persona id, each as the
    tag's bytes in Base64. Entries that hold anything else are left out.

    Args:
        entries: The record's entries by key.
    """
    tags = []
    for key, value in entries.items():
        try:
            name, days, gold, silver, bronze, rank = _TAG.unpack(b64decode(value, validate=True))
        except binascii.Error, struct.error:
            continue
        # A day count the game cannot have written, NaN included, marks a broken entry.
        if not key.isdecimal() or not 0 <= days < 100_000:
            continue
        tags.append(
            DogTag(
                persona_id=int(key),
                name=name.split(b"\0", 1)[0].decode("utf-8", "replace"),
                rank=rank,
                taken=EPOCH + timedelta(days=days),
                count=gold + silver + bronze,
            )
        )
    return sorted(tags, key=lambda tag: tag.taken, reverse=True)
