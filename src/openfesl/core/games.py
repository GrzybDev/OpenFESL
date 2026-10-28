import tomllib
from collections.abc import Iterable, Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from openfesl.core.game_pages import Listing, PageKind, Stats, Theme
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
        entitlement: The entitlement a player's account needs to play, with its product when it
            names one; none for dedicated servers.
        server_account: The account that every dedicated server of the game logs in with, as
            its game data names it; only dedicated servers may use it.
        server_client: The client that a Hello with this client string stands for when it
            declares the client type server, for dedicated servers that greet with the
            game's own client string.
    """

    model_config = ConfigDict(frozen=True)

    theater_port: int
    entitlement: Grant | None = None
    server_account: str | None = None
    server_client: str | None = None


class Grant(BaseModel):
    """An entitlement as a product key or a dedicated server grants it, or a client needs it."""

    model_config = ConfigDict(frozen=True)

    tag: str
    group: str | None = None
    product: str | None = None

    def provides(self, needed: Grant) -> bool:
        """Tell whether this grant gives a needed entitlement, comparing products without case.

        Args:
            needed: The entitlement that is needed.
        """
        product = needed.product
        return self.tag == needed.tag and (
            product is None or (self.product or "").casefold() == product.casefold()
        )


class ProductKey(BaseModel):
    """A product key of a game's starting set and what it grants."""

    model_config = ConfigDict(frozen=True)

    code: str
    entitlements: list[Grant]


class Http(BaseModel):
    """What a game fetches over HTTP from EA's web servers, which the hook sends here instead.

    Attributes:
        locker: The URL that GetLockerURL hands out, where the server answers the locker.
        magma: The host of the game's Magma web services, which the server answers as a whole:
            the account layer, where the game fetches the login token of the launcher session it
            was started with, the store and the website; none for a game that logs in over FESL
            alone.
        files: The editorial files by URL, as UTF-8 templates whose `${news_<language>}`
            placeholders take the news text in that language.
    """

    model_config = ConfigDict(frozen=True)

    locker: str | None = None
    magma: str | None = None
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
        pages: The kinds of web pages that the game's menu shows, in its order, and the site
            serves besides the game's front page.
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
    pages: list[PageKind]
    theme: Theme
    listing: Listing = Listing()
    stats: Stats = Stats()
    product_names: dict[str, str] = {}

    @property
    def play_key(self) -> ProductKey | None:
        """The starting product key that grants what the game's players need to play, if any."""
        needed = [client.entitlement for client in self.clients.values() if client.entitlement]
        return next(
            (
                key
                for key in self.keys
                if any(grant.provides(need) for grant in key.entitlements for need in needed)
            ),
            None,
        )

    @property
    def launched(self) -> bool:
        """Whether the game logs in with a launcher's sign-in instead of on a screen of its own.

        Such a game is started with the launcher's session, which its Magma host trades for
        the token it logs in with.
        """
        return self.http.magma is not None

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

    @model_validator(mode="after")
    def _check_server_clients(self) -> Self:
        for name, client in self.clients.items():
            if client.server_client is None:
                continue
            server = self.clients.get(client.server_client)
            if server is None or server.server_account is None:
                raise ValueError(
                    f"client {name} names {client.server_client} for its dedicated servers, "
                    "which is no client with a server account"
                )
        return self

    @model_validator(mode="after")
    def _check_host_page(self) -> Self:
        if "host" in self.pages and self.server_files is None:
            raise ValueError("the host page needs the server_files that hosting involves")
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

    def with_page(self, kind: PageKind) -> list[Game]:
        """Return the games that list a kind of web page, which the site serves for them only.

        Args:
            kind: The kind of page.
        """
        return [game for game in self._games if kind in game.pages]

    def find(self, client_string: str) -> tuple[Game, Client] | None:
        """Return the game and client that a client string names, if the server serves it.

        Args:
            client_string: The `clientString` of a FESL Hello.
        """
        return self._clients.get(client_string)


def load_games(folder: Traversable | None = None) -> Games:
    """Read every game's TOML data file and the editorial files it names, in the files' order.

    Args:
        folder: The folder of data files; the games that ship with the server when omitted.
    """
    folder = folder or files("openfesl") / "games"
    # A folder lists its files in an order of the file system's own.
    entries = sorted(folder.iterdir(), key=lambda entry: entry.name)
    return Games(_load(entry, folder) for entry in entries if entry.name.endswith(".toml"))


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
