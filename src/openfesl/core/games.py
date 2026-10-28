import re
import tomllib
from collections.abc import Iterable, Iterator
from enum import StrEnum
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from openfesl.core.catalogue import CatalogueRules
from openfesl.core.chests import ChestRules
from openfesl.core.deal_rules import DealRules, OfferSection
from openfesl.core.game_pages import Listing, PageKind, Site, Stats, Theme
from openfesl.core.hero_rules import HeroRules
from openfesl.core.policies import Policies
from openfesl.core.relationship_rules import RelationshipRules
from openfesl.core.reward_rules import RewardRules
from openfesl.core.store_rules import StoreRules
from openfesl.core.texts import BASE_LANGUAGE


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
        servers_log_in_as_players: Whether the game's dedicated servers log in as this client
            does, each with a player's account and persona, so that only the game a session
            hosts tells a server from a player.
    """

    model_config = ConfigDict(frozen=True)

    theater_port: int
    entitlement: Grant | None = None
    server_account: str | None = None
    server_client: str | None = None
    servers_log_in_as_players: bool = False


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
        languages: The languages that the Magma addresses name otherwise than the game's
            language table, by the addresses' name, such as br for pt-BR.
    """

    model_config = ConfigDict(frozen=True)

    locker: str | None = None
    magma: str | None = None
    files: dict[str, str] = {}
    languages: dict[str, str] = {}


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


class Scope(StrEnum):
    """Who holds a balance in a currency: an account for all its personas, or each its own."""

    ACCOUNT = "account"
    PERSONA = "persona"


class Credits(BaseModel):
    """What a game's dedicated servers may credit to a balance in a currency.

    Attributes:
        most: The most they credit to one balance within the window; a credit beyond it is
            credited up to it.
        within: The window, in seconds.
    """

    model_config = ConfigDict(frozen=True)

    most: NonNegativeInt
    within: PositiveInt


class Currency(BaseModel):
    """A currency that a game's players spend from their wallets.

    Attributes:
        scope: Who holds a balance in it.
        allowance: What each balance in it starts with.
        credits: What the game's dedicated servers may credit to its balances; none for a
            currency they may not credit.
    """

    model_config = ConfigDict(frozen=True)

    scope: Scope
    allowance: int = 0
    credits: Credits | None = None


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
        currencies: The currencies of the players' wallets, by the code the game knows each by.
        store: How the game's store prices and sells the items of its catalogue; none for a
            game without one.
        deals: How the store's hot deals change by themselves; none for a game whose hot deals
            are the admin's alone.
        rewards: How the game's site gives rewards that accounts claim; none for a game
            without them.
        account_stats: The stats that belong to a player's account rather than to one of its
            personas, by patterns that their keys match whole.
        records: The records a persona may keep, by name, with the most entries each holds.
        associations: The association list types the game keeps, by name.
        relationships: The relationships between accounts that the game keeps through Magma;
            none for a game without them.
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
        site: The look of the game's own website, which its pages wear instead of the game's
            menus; none for a game whose pages look like its menus.
        listing: How the web pages read the games its servers host.
        stats: The stats of the game's players that the web pages show.
        product_names: The names the game's data gives the products of some entitlements, by
            tag; a game that redeems a key in its menu refreshes the content it finds by the
            name of the answer's first product.
        catalogue: Where a copy of the game keeps its catalogue of items, which the server
            imports from a player's copy, and what it means; none for a game without one.
        heroes: How the game's heroes are made on its site and kept; none for a game whose
            personas are made in the game.
        chests: How the game's servers stack treasure chests on heroes and keys open them;
            none for a game without them.
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
    currencies: dict[str, Currency] = {}
    store: StoreRules | None = None
    deals: DealRules | None = None
    rewards: RewardRules | None = None
    account_stats: list[re.Pattern[str]] = []
    records: dict[str, int] = {}
    associations: dict[str, ListRule] = {}
    relationships: RelationshipRules | None = None
    lobbies: list[Lobby] = []
    provider_attribute: str | None = None
    policies: Policies
    countries: dict[str, Ages] = {}
    pages: list[PageKind]
    theme: Theme
    site: Site | None = None
    listing: Listing = Listing()
    stats: Stats = Stats()
    product_names: dict[str, str] = {}
    catalogue: CatalogueRules | None = None
    heroes: HeroRules | None = None
    chests: ChestRules | None = None

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

    def language_of(self, segment: str) -> str:
        """Return the language that a Magma address names, as the game's language table names it.

        A language that the table lacks is English, so that only the game's own languages reach
        the texts read for an address.

        Args:
            segment: The language that the address starts with, such as en or br.
        """
        language = self.http.languages.get(segment, segment)
        known = self.catalogue.languages.values() if self.catalogue else ()
        return language if language in known else BASE_LANGUAGE

    def owned_by_account(self, key: str) -> bool:
        """Tell whether a stat belongs to a player's account rather than to one of its personas.

        Args:
            key: The stat's key.
        """
        return any(pattern.fullmatch(key) for pattern in self.account_stats)

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
    def _check_site(self) -> Self:
        # The bar shows an account's balances, which only currencies its personas share hold.
        shared = {
            code for code, currency in self.currencies.items() if currency.scope is Scope.ACCOUNT
        }
        shown = {wallet.currency for wallet in self.site.wallets} if self.site else set()
        if unknown := shown - shared:
            raise ValueError(
                f"the site shows wallets that are not the account's: {sorted(unknown)}"
            )
        return self

    @model_validator(mode="after")
    def _check_heroes(self) -> Self:
        points = self.currencies.get(self.heroes.points) if self.heroes else None
        if self.heroes and (points is None or points.scope is not Scope.PERSONA):
            raise ValueError("the heroes' points need a currency that each persona holds")
        paid = self.currencies.get(self.heroes.currency) if self.heroes else None
        if self.heroes and (paid is None or paid.scope is not Scope.ACCOUNT):
            raise ValueError("the heroes' prices need a currency that the account holds")
        # The XP table runs from level 0 to the one below the highest, which leads nowhere.
        table = self.catalogue.experience.levels if self.catalogue else None
        if self.heroes and table is not None and self.heroes.highest_level != table:
            raise ValueError("the heroes' highest level must end the catalogue's XP table")
        return self

    @model_validator(mode="after")
    def _check_store(self) -> Self:
        rules = self.store.prices if self.store else []
        boxes = self.store.boxes if self.store else []
        used = {rule.currency for rule in rules} | {box.currency for box in boxes}
        if unknown := used - self.currencies.keys():
            raise ValueError(f"the store prices items in unknown currencies: {sorted(unknown)}")
        factions = set(self.heroes.factions) if self.heroes else set()
        classes = set(self.heroes.classes) if self.heroes else set()
        for box in boxes:
            if box.faction not in {None, *factions} or box.hero_class not in {None, *classes}:
                raise ValueError(f"the box {box.key} names a faction or class the heroes lack")
        return self

    @model_validator(mode="after")
    def _check_deals(self) -> Self:
        if self.deals is None:
            return self
        # Deals are priced from the store's products and made for the heroes' factions.
        if self.store is None or self.heroes is None:
            raise ValueError("hot deals need a store and heroes")
        if self.deals.currency not in self.currencies:
            raise ValueError(f"hot deals are priced in an unknown currency: {self.deals.currency}")
        offers = [
            offer
            for section in self.deals.sections.values()
            if isinstance(section, OfferSection)
            for offer in section.offers
        ]
        boxes = {box.key for box in self.store.boxes if box.kind == "box"}
        if unknown := {offer.box for offer in offers if offer.box} - boxes:
            raise ValueError(f"hot deals sell boxes the store lacks: {sorted(unknown)}")
        return self

    @model_validator(mode="after")
    def _check_rewards(self) -> Self:
        if self.rewards is None:
            return self
        # Rewards give items to heroes, Funds to the account, box drops and chest keys.
        currency = self.currencies.get(self.rewards.currency)
        if self.heroes is None or currency is None or currency.scope is not Scope.ACCOUNT:
            raise ValueError("rewards need heroes and Funds that the account holds")
        rewards = self.rewards
        parts = [part for event in rewards.events.values() for part in event.parts.values()]
        packs = rewards.starter.packs if rewards.starter else {}
        gifts = rewards.gifts
        factions = {part.faction for part in parts} | set(packs)
        classes = {name for found in packs.values() for name in found}
        classes |= {part.goal.hero_class for part in parts if part.goal and part.goal.hero_class}
        if factions - {None, *self.heroes.factions} or classes - set(self.heroes.classes):
            raise ValueError("rewards name a faction or class the heroes lack")
        boxes = {box.key for box in self.store.boxes} if self.store else set()
        if unknown := {gift.box for gift in gifts if gift.box} - boxes:
            raise ValueError(f"rewards give boxes the store lacks: {sorted(unknown)}")
        if self.chests is None and any(gift.keys for gift in gifts):
            raise ValueError("rewards give keys of a game without treasure chests")
        withheld = self.catalogue.withheld if self.catalogue else frozenset()
        if given := sorted(rewards.given_items & withheld):
            raise ValueError(f"rewards give withheld items: {given}")
        return self

    @model_validator(mode="after")
    def _check_chests(self) -> Self:
        if self.chests is None:
            return self
        currency = self.currencies.get(self.chests.currency)
        if currency is None or currency.scope is not Scope.ACCOUNT:
            raise ValueError("keys need a currency that the account holds")
        boxes = {box.key for box in self.store.boxes} if self.store else set()
        if self.chests.contents not in boxes:
            raise ValueError(f"the chests' contents name no box: {self.chests.contents}")
        return self

    @model_validator(mode="after")
    def _check_host_page(self) -> Self:
        if "host" in self.pages and self.server_files is None:
            raise ValueError("the host page needs the server_files that hosting involves")
        return self

    @model_validator(mode="after")
    def _check_chests_page(self) -> Self:
        if "treasure-chests" in self.pages and not (self.chests and self.heroes and self.site):
            raise ValueError("the treasure chests page needs chests, heroes and a site")
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
    # A data file may keep parts of the game's data in files of their own, each part in one.
    for name in data.pop("include", []):
        part = tomllib.loads(folder.joinpath(*name.split("/")).read_text("utf-8"))
        if twice := data.keys() & part.keys():
            raise ValueError(f"{name} defines {sorted(twice)} again")
        data |= part
    http = data.get("http", {})
    # The data file names each editorial file by its path in the folder.
    contents = {
        url: folder.joinpath(*name.split("/")).read_text("utf-8")
        for url, name in http.get("files", {}).items()
    }
    game_id = entry.name.removesuffix(".toml")
    return Game.model_validate(data | {"id": game_id, "http": http | {"files": contents}})
