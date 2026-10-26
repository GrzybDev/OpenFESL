import tomllib
from bisect import bisect_right
from collections.abc import Iterable, Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from openfesl.core.policies import Policies


class Partition(BaseModel):
    """The domain partition that holds a game's data, written `/domain/subDomain`."""

    model_config = ConfigDict(frozen=True)

    domain: str
    sub_domain: str


class Client(BaseModel):
    """One client string of a game, such as the game on a platform or its dedicated server.

    Attributes:
        theater_port: The Theater port that the Hello answer names.
        entitlement: The tag a player's account needs to play; none for dedicated servers.
        server_account: The account that every dedicated server of the game logs in with, as
            its game data names it; only dedicated servers may use it.
    """

    model_config = ConfigDict(frozen=True)

    theater_port: int
    entitlement: str | None = None
    server_account: str | None = None


class Grant(BaseModel):
    """An entitlement that a product key or a dedicated server grants."""

    model_config = ConfigDict(frozen=True)

    tag: str
    group: str | None = None
    product: str | None = None


class ProductKey(BaseModel):
    """A product key of a game's starting set and what it grants."""

    model_config = ConfigDict(frozen=True)

    code: str
    entitlements: list[Grant]


class Http(BaseModel):
    """What a game fetches over HTTP from EA's web servers, which the hook sends here instead.

    Attributes:
        locker: The URL that GetLockerURL hands out, where the server answers the locker.
        files: The editorial files by URL, as UTF-8 templates whose `${news_<language>}`
            placeholders take the news text in that language.
    """

    model_config = ConfigDict(frozen=True)

    locker: str | None = None
    files: dict[str, str] = {}


class ListRule(BaseModel):
    """How the server keeps one type of a game's association lists.

    Attributes:
        size: The most members one list holds.
        mutual: Whether members are on each other's lists: a member gains the owner on its own
            list once it agreed, and loses the owner whenever either removes the other.
        kept_by_servers: Whether the game's dedicated servers keep the lists for their
            players, who may only read their own.
        blocks: Whether a list's members are kept away from its owner, who stays hidden
            from them.
        invitations: The message types with which a player asks another onto a mutual list;
            sending one agrees to the other's add.
    """

    model_config = ConfigDict(frozen=True)

    size: int
    mutual: bool = False
    kept_by_servers: bool = False
    blocks: bool = False
    invitations: tuple[str, ...] = ()


class Ages(BaseModel):
    """The age limits of a country on registration.

    Attributes:
        registration_age: The age from which a player may register.
        parental_age: The age from which a player registers without a parent's consent.
    """

    model_config = ConfigDict(frozen=True)

    registration_age: int = 13
    parental_age: int = 18


class Lobby(BaseModel):
    """A Theater lobby that a game's hosts list their games in."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    locale: str
    max_games: int


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


class ServerFiles(BaseModel):
    """The files of a game's dedicated server release that hosting on OpenFESL involves.

    Attributes:
        executable: The server's executable, next to which OpenFESLHook goes.
        config: The configuration file in the server's folder, next to which the provider
            file goes.
        provider: The file that names the operator who runs the server.
        identity: The file in the server's instance folder in which the server keeps the
            identity OpenFESL issued it.
    """

    model_config = ConfigDict(frozen=True)

    executable: str
    config: str
    provider: str
    identity: str


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


class Game(BaseModel):
    """A game the server serves, as its data file describes it.

    Attributes:
        id: The name of its data file, which the instance's settings know it by.
        name: The game's name.
        executable: The file name of the game's executable, next to which OpenFESLHook goes.
        server_files: The files of the game's dedicated server release, for a game whose players
            may host servers.
        partition: Where the game's data lives.
        namespace: The persona namespace; persona names are unique within it.
        clients: The game's client strings.
        keys: The product keys the database starts with; the admin manages them from there.
        automatic_grants: The entitlements that the game's dedicated servers may grant the
            players in their games, by tag.
        http: What the game fetches over HTTP.
        records: The records a persona may keep, by name, with the most entries each holds.
        associations: The association list types the game keeps, by name.
        lobbies: The Theater lobbies of all of the game's clients.
        provider_attribute: The basic game attribute in which dedicated servers report their
            provider and game lists show the provider's name; hosts cannot set it themselves.
        policies: How the game's services behave by default where the game handles more than
            one behaviour.
        countries: The age limits of the countries whose limits differ from the defaults, by
            ISO code.
        theme: How the game's web pages look.
        listing: How the web pages read the games its servers host.
        stats: The stats of the game's players that the web pages show.
        product_names: The names the game's data gives the products of some entitlements, by
            tag; a game that redeems a key in its menu refreshes the content it finds by the
            name of the answer's first product.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    executable: str
    server_files: ServerFiles | None = None
    partition: Partition
    namespace: str
    clients: dict[str, Client]
    keys: list[ProductKey] = []
    automatic_grants: list[Grant] = []
    http: Http = Http()
    records: dict[str, int] = {}
    associations: dict[str, ListRule] = {}
    lobbies: list[Lobby] = []
    provider_attribute: str | None = None
    policies: Policies
    countries: dict[str, Ages] = {}
    theme: Theme
    listing: Listing = Listing()
    stats: Stats = Stats()
    product_names: dict[str, str] = {}

    @property
    def play_key(self) -> ProductKey | None:
        """The starting product key that grants what the game's players need to play, if any."""
        tags = {client.entitlement for client in self.clients.values() if client.entitlement}
        return next(
            (key for key in self.keys if any(grant.tag in tags for grant in key.entitlements)),
            None,
        )

    def ages(self, country: str) -> Ages:
        """Return the age limits of a country on registration.

        Args:
            country: The country's ISO code, in either case.
        """
        return self.countries.get(country.upper(), Ages())

    @field_validator("partition", mode="before")
    @classmethod
    def _read_partition(cls, value: object) -> object:
        if isinstance(value, str):
            empty, domain, sub_domain = value.split("/")
            if empty:
                raise ValueError(f"partition {value!r} does not start with '/'")
            return Partition(domain=domain, sub_domain=sub_domain)
        return value

    @model_validator(mode="after")
    def _check_tiers(self) -> Self:
        for code, insignia in self.stats.awards.insignias.items():
            if insignia.tier > len(self.theme.tiers):
                raise ValueError(f"insignia {code} has tier {insignia.tier}, which the theme lacks")
        return self


class Games:
    """The games the server serves, found by the client string of a FESL Hello."""

    def __init__(self, games: Iterable[Game]) -> None:
        """Index the games by their client strings.

        Args:
            games: Every game the server serves.

        Raises:
            ValueError: When two games claim the same client string.
        """
        self._games = list(games)
        self._clients: dict[str, tuple[Game, Client]] = {}
        for game in self._games:
            for name, client in game.clients.items():
                if name in self._clients:
                    raise ValueError(f"client string {name} belongs to two games")
                self._clients[name] = (game, client)

    def __iter__(self) -> Iterator[Game]:
        return iter(self._games)

    @property
    def server_accounts(self) -> list[tuple[Game, str]]:
        """Return the accounts that the games' dedicated servers log in with, by game."""
        return [
            (game, client.server_account)
            for game, client in self._clients.values()
            if client.server_account
        ]

    @property
    def keys(self) -> list[ProductKey]:
        """Return the starting product keys of every game."""
        return [key for game in self._games for key in game.keys]

    def by_id(self, game_id: str) -> Game | None:
        """Return the game whose data file has a name, if the server serves it.

        Args:
            game_id: The data file's name without its extension.
        """
        return next((game for game in self._games if game.id == game_id), None)

    def find(self, client_string: str) -> tuple[Game, Client] | None:
        """Return the game and client that a client string names, if the server serves it.

        Args:
            client_string: The `clientString` of a FESL Hello.
        """
        return self._clients.get(client_string)


def load_games(folder: Traversable | None = None) -> Games:
    """Read every game's TOML data file and the editorial files it names.

    Args:
        folder: The folder of data files; the games that ship with the server when omitted.
    """
    folder = folder or files("openfesl") / "games"
    return Games(_load(entry, folder) for entry in folder.iterdir() if entry.name.endswith(".toml"))


def _load(entry: Traversable, folder: Traversable) -> Game:
    data = tomllib.loads(entry.read_text("utf-8"))
    http = data.get("http", {})
    # The data file names each editorial file by its path in the folder.
    contents = {
        url: folder.joinpath(*name.split("/")).read_text("utf-8")
        for url, name in http.get("files", {}).items()
    }
    game_id = entry.name.removesuffix(".toml")
    return Game.model_validate(data | {"id": game_id, "http": http | {"files": contents}})
