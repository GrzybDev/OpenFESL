from bisect import bisect_right
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PositiveInt, StringConstraints

# CSS colours only, so that a data file puts nothing else into the themes' stylesheet.
type Colour = Annotated[str, StringConstraints(pattern=r"^(#[0-9A-Fa-f]{6}|rgba\([0-9., ]+\))$")]


class Theme(BaseModel):
    """How a game's web pages look: the style of the game's own menus, drawn by OpenFESL.

    Attributes:
        font: The typeface of the menus' capitals, one of those the web pages ship.
        accent: The colour of the title block, selections and action squares; black text
            goes on it.
        accent_hover: The accent of an action square under the pointer.
        menu_current: The block behind the menu bar's current item.
        badge: The small badges of a server's state, with a black glyph on them.
        badge_edge: The badges' border.
        field: The backdrop behind the game's pages.
        mottle: The backdrop's coarse grain.
        grit: The backdrop's fine grain.
        panel: The large container behind tables and forms.
        panel_edge: The container's border.
        row: Rows and notes; black text goes on them.
        row_alt: Every other row of a table.
        row_light: Text boxes and light rows.
        score_row: The rows of a scoreboard; black text goes on them.
        score_row_alt: Every other row of a scoreboard.
        subtitle: The bar under the title block.
        bright: Text on the backdrop and on black bars.
        soft: Secondary text on the backdrop.
        tiers: The fill and glyph colour of earned insignias by their tier, from tier 1.
    """

    model_config = ConfigDict(frozen=True)

    font: Annotated[str, StringConstraints(pattern=r"^[A-Za-z ]+$")]
    accent: Colour
    accent_hover: Colour
    menu_current: Colour
    badge: Colour
    badge_edge: Colour
    field: Colour
    mottle: Colour
    grit: Colour
    panel: Colour
    panel_edge: Colour
    row: Colour
    row_alt: Colour
    row_light: Colour
    score_row: Colour
    score_row_alt: Colour
    subtitle: Colour
    bright: Colour
    soft: Colour
    tiers: list[tuple[Colour, Colour]] = []


class Names(BaseModel):
    """An attribute of hosted games whose values the web pages name, such as the map.

    Attributes:
        attribute: The attribute, matched regardless of case.
        names: The names of its values, by value; a name is the end of a text's key in the
            catalogues. Values match regardless of case.
        prefix: Whether a value takes the name of the longest of these values it starts with, as
            when a map is played in several modes under ids that share their start.
        otherwise: The name of every other value; other values are shown as they are without one.
    """

    model_config = ConfigDict(frozen=True)

    attribute: str
    names: dict[str, str] = {}
    prefix: bool = False
    otherwise: str | None = None

    def name_of(self, value: str) -> str | None:
        """Return the name of one of the attribute's values, if the game data names it.

        Args:
            value: The value as a host sent it.
        """
        folded = value.casefold()
        matching = [
            known
            for known in self.names
            if (folded.startswith(known.casefold()) if self.prefix else folded == known.casefold())
        ]
        return self.names[max(matching, key=len)] if matching else self.otherwise


class Flag(BaseModel):
    """An attribute of hosted games that marks a game while it holds a value."""

    model_config = ConfigDict(frozen=True)

    attribute: str
    value: str


type Field = Literal["name", "score", "kills", "deaths", ""]


class Roster(BaseModel):
    """The players a server lists in numbered attributes, one player each.

    Attributes:
        attribute: The attributes' name before their two-digit number, such as D-pdat for
            D-pdat00, D-pdat01 and on.
        separator: What separates the fields of one player.
        fields: What each field holds, in order: the player's name or a number of the player's
            round; an empty string for a field whose meaning is not known, which no page shows.
    """

    model_config = ConfigDict(frozen=True)

    attribute: str
    separator: str = "|"
    fields: list[Field]


class Listing(BaseModel):
    """How the web pages read the games that a game's servers host.

    Attributes:
        squad: How many players a squad holds, which a server needs free to have room for one.
        map: The map a game is played on.
        mode: The game mode, whose names the server list offers in this order.
        expansion: The content a game needs, such as an expansion.
        region: Where the server is.
        hardcore: The mark of a game played under hardcore rules.
        message: The attributes that hold the server's message in pieces, named by a number
            after this name, with their count in the attribute of this name and Count.
        settings: The attributes of the game's settings that are on or off, by the settings'
            names in the catalogues, in the order the server's page shows them.
        roster: The players the server lists, with what it lists about each.
    """

    model_config = ConfigDict(frozen=True)

    squad: int = 1
    map: Names | None = None
    mode: Names | None = None
    expansion: Names | None = None
    region: Names | None = None
    hardcore: Flag | None = None
    message: str | None = None
    settings: dict[str, str] = {}
    roster: Roster | None = None


# The id of a glyph in the web pages' icons.
type Glyph = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z-]*$")]


class Insignia(BaseModel):
    """How a player's page draws an insignia.

    Attributes:
        glyph: The glyph of what the insignia is earned for.
        tier: The rank word in the insignia's name, from 1 for none; it picks the insignia's
            colours from the game's theme.
    """

    model_config = ConfigDict(frozen=True)

    glyph: Glyph
    tier: PositiveInt = 1


class Awards(BaseModel):
    """The awards a player's page lists in this order, by their codes in the game's stats.

    Attributes:
        count: The key of the stat that counts how often an award was earned, with {code}
            standing for the award's code.
        pins: The pins, which players earn again and again, each with the glyph of what it is
            earned for.
        insignias: The insignias, which players earn once.
    """

    model_config = ConfigDict(frozen=True)

    count: str = "{code}"
    pins: dict[str, Glyph] = {}
    insignias: dict[str, Insignia] = {}

    def key(self, code: str) -> str:
        """Return the key of the stat that counts an award.

        Args:
            code: The award's code.
        """
        return self.count.replace("{code}", code)


class Stats(BaseModel):
    """The stats the web pages show, each as the keys of the game's stats whose values it sums.

    Dedicated servers store each key as an offset from its default in the game's own stats
    template, so these name only keys whose default is 0.

    Attributes:
        score: The total score.
        award_score: The part of the total score that awards earned.
        kills: The kills.
        deaths: The deaths.
        dog_tags: The dog tags taken, every time counted.
        time: The time played, in seconds.
        kits: The score earned with each kit or in vehicles, by its name in the catalogues.
        ranks: The total score each rank needs, from rank 1 on; rank 0 needs none.
        awards: The awards players earn.
        dog_tag_record: The record in which a player's game keeps the dog tags it took, one per
            player whose tag it took.
    """

    model_config = ConfigDict(frozen=True)

    score: list[str] = []
    award_score: list[str] = []
    kills: list[str] = []
    deaths: list[str] = []
    dog_tags: list[str] = []
    time: list[str] = []
    kits: dict[str, list[str]] = {}
    ranks: list[int] = []
    awards: Awards = Awards()
    dog_tag_record: str | None = None

    def rank(self, score: float) -> int:
        """Return the rank that a total score reaches.

        Args:
            score: The total score.
        """
        return bisect_right(self.ranks, score)

    def needed(self, rank: int) -> int:
        """Return the total score a rank needs.

        Args:
            rank: The rank, from 0.
        """
        return self.ranks[rank - 1] if rank > 0 else 0

    def sums(self) -> dict[str, list[str]]:
        """Return the keys of each stat the leaderboard shows, by the stat's name."""
        return {
            "score": self.score,
            "kills": self.kills,
            "deaths": self.deaths,
            "dog_tags": self.dog_tags,
            "time": self.time,
        }

    def profile(self) -> dict[str, list[str]]:
        """Return the keys of each stat a player's page shows, by the stat's name."""
        kits = {f"kit_{name}": keys for name, keys in self.kits.items()}
        return self.sums() | {"award_score": self.award_score} | kits
