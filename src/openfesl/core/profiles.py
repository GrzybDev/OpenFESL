from pydantic import BaseModel, ConfigDict, PositiveInt


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
        rank: The prestige rank, for a game whose heroes have one.
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
    rank: Rank | None = None

    @property
    def texts(self) -> set[str]:
        """The ids of the texts of the game's language table that profiles show."""
        return self.rank.texts if self.rank else set()
