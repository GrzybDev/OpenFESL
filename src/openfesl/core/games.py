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
        server_account: The account that every dedicated server of the game logs in with, as
            its game data names it; only dedicated servers may use it.
    """

    model_config = ConfigDict(frozen=True)

    theater_port: int
    server_account: str | None = None


class Game(BaseModel):
    """A game the server serves, as its data file describes it."""

    model_config = ConfigDict(frozen=True)

    name: str
    partition: Partition
    clients: dict[str, Client]

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
    def server_accounts(self) -> list[str]:
        """Return the accounts that the games' dedicated servers log in with."""
        clients = (client for _, client in self._clients.values())
        return [client.server_account for client in clients if client.server_account]

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
