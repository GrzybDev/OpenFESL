import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, PositiveInt

# How the {id} of a pattern reads once escaped.
_BRACED = re.escape("{id}")


class Rank(BaseModel):
    """The prestige rank that a stat of a hero packs: its level in the low byte, its rotation above.

    Attributes:
        key: The stat's key.
        level: The id of each level's name in the game's language table, with {n} for the level.
        rotation: The id of each rotation's numeral, likewise.
        levels: The highest level.
        rotations: The highest rotation.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    level: str
    rotation: str
    levels: PositiveInt
    rotations: PositiveInt

    def of(self, value: float) -> tuple[str, str] | None:
        """Return the ids of the names of the level and rotation that a stat holds, if any.

        Args:
            value: The stat's value; a hero below the first level has none.
        """
        level, rotation = int(value) & 0xFF, int(value) >> 8
        if not 1 <= level <= self.levels or rotation > self.rotations:
            return None
        named = self.rotation.replace("{n}", str(rotation)) if rotation else ""
        return self.level.replace("{n}", str(level)), named

    @property
    def texts(self) -> set[str]:
        """The ids of every level's and rotation's name."""
        levels = {self.level.replace("{n}", str(n)) for n in range(1, self.levels + 1)}
        return levels | {self.rotation.replace("{n}", str(n)) for n in range(1, self.rotations + 1)}


class Breakdown(BaseModel):
    """Stats that a game keeps of each of the maps, weapons, vehicles or moments of a hero.

    Attributes:
        keys: The pattern of each stat's key by the stat's name, with {id} for the number of the
            map, weapon, vehicle or moment.
        names: The name of each by its number: the id of a text of the game's language table, or
            for vehicles OpenFESL's own word for it; one without a name shows its number.
    """

    model_config = ConfigDict(frozen=True)

    keys: dict[str, str]
    names: dict[int, str] = {}

    def read(self, values: Mapping[str, float]) -> list[tuple[int, dict[str, float]]]:
        """Return the stats of each one that a hero's stats name, by number.

        Args:
            values: The hero's stats by key; those never stored count as 0.
        """
        found: dict[int, dict[str, float]] = {}
        for name, pattern in self.keys.items():
            numbered = re.compile(re.escape(pattern).replace(_BRACED, "([0-9]+)"))
            for key, value in values.items():
                if matched := numbered.fullmatch(key):
                    found.setdefault(int(matched[1]), dict.fromkeys(self.keys, 0.0))[name] = value
        return sorted(found.items())


class ProfileRules(BaseModel):
    """The stats of a hero that its profile shows, by the keys of the game's stats.

    Attributes:
        rating: The skill rating.
        kills: The kills.
        deaths: The deaths.
        hits: The shots that hit.
        shots: The shots fired.
        suicides: The suicides.
        score: The score.
        wins: The rounds won.
        losses: The rounds lost.
        captures: The flags captured and neutralised.
        rocket_captures: The rockets captured.
        time: The time played, in seconds; a hero with none has played no round.
        games: The rounds played.
        rank: The prestige rank, for a game whose heroes have one.
        current: The keys of the missions that the hero takes on now, one for each mission slot.
        weapons: Each weapon's time used in seconds, kills, deaths while using it, shots that hit
            and shots fired, by its item number.
        foes: The deaths from each weapon that killed the hero, by its item number.
        maps: Each map's time in seconds, wins and losses.
        vehicles: Each vehicle's time in seconds, kills, kills by running into foes, vehicles
            destroyed, deaths from it and deaths inside it.
        moments: How often the hero had each heroic moment.
    """

    model_config = ConfigDict(frozen=True)

    rating: str
    kills: str
    deaths: str
    hits: str
    shots: str
    suicides: str
    score: str
    wins: str
    losses: str
    captures: str
    rocket_captures: str
    time: str
    games: str
    rank: Rank | None = None
    current: list[str] = []
    weapons: Breakdown
    foes: Breakdown
    maps: Breakdown
    vehicles: Breakdown
    moments: Breakdown

    @property
    def texts(self) -> set[str]:
        """The ids of the texts of the game's language table that profiles show."""
        named = {*self.maps.names.values(), *self.moments.names.values()}
        return named | (self.rank.texts if self.rank else set())
