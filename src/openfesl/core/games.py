import tomllib
from collections.abc import Iterable
from importlib.resources import files
from importlib.resources.abc import Traversable

from pydantic import BaseModel, ConfigDict, field_validator


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
    """An entitlement that a product key grants."""

    model_config = ConfigDict(frozen=True)

    tag: str
    group: str | None = None
    product: str | None = None


class ProductKey(BaseModel):
    """A product key of a game's starting set and what it grants."""

    model_config = ConfigDict(frozen=True)

    code: str
    entitlements: list[Grant]


class Game(BaseModel):
    """A game the server serves, as its data file describes it.

    Attributes:
        name: The game's name.
        partition: Where the game's data lives.
        namespace: The persona namespace; persona names are unique within it.
        clients: The game's client strings.
        keys: The product keys the database starts with; the admin manages them from there.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    partition: Partition
    namespace: str
    clients: dict[str, Client]
    keys: list[ProductKey] = []

    @field_validator("partition", mode="before")
    @classmethod
    def _read_partition(cls, value: object) -> object:
        if isinstance(value, str):
            empty, domain, sub_domain = value.split("/")
            if empty:
                raise ValueError(f"partition {value!r} does not start with '/'")
            return Partition(domain=domain, sub_domain=sub_domain)
        return value


class Games:
    """The games the server serves, found by the client string of a FESL Hello."""

    def __init__(self, games: Iterable[Game]) -> None:
        """Index the games by their client strings.

        Args:
            games: Every game the server serves.

        Raises:
            ValueError: When two games claim the same client string.
        """
        self._clients: dict[str, tuple[Game, Client]] = {}
        for game in games:
            for name, client in game.clients.items():
                if name in self._clients:
                    raise ValueError(f"client string {name} belongs to two games")
                self._clients[name] = (game, client)

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
        games = {id(game): game for game, _ in self._clients.values()}
        return [key for game in games.values() for key in game.keys]

    def find(self, client_string: str) -> tuple[Game, Client] | None:
        """Return the game and client that a client string names, if the server serves it.

        Args:
            client_string: The `clientString` of a FESL Hello.
        """
        return self._clients.get(client_string)


def load_games(folder: Traversable | None = None) -> Games:
    """Read every game's TOML data file.

    Args:
        folder: The folder of data files; the games that ship with the server when omitted.
    """
    folder = folder or files("openfesl") / "games"
    return Games(
        Game.model_validate(tomllib.loads(entry.read_text("utf-8")))
        for entry in folder.iterdir()
        if entry.name.endswith(".toml")
    )
