import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from openfesl.core.games import Flag, Game, Names
from openfesl.core.hosting import GAME, HostedGame, Hosting
from openfesl.core.session import Players

_NOT_IN_SLUGS = re.compile(r"[\W_]+")


@dataclass(frozen=True, slots=True)
class Activity:
    """How many play a game now.

    Attributes:
        online: The players logged in to the game.
        in_matches: Those among them in a game that a server hosts.
        servers: The games that servers host and players see.
    """

    online: int
    in_matches: int
    servers: int

    @property
    def in_menus(self) -> int:
        """The players logged in who are in no hosted game."""
        return max(self.online - self.in_matches, 0)


@dataclass(frozen=True, slots=True)
class Label:
    """A hosted game's attribute as the pages show it.

    Attributes:
        name: The end of its name's key in the catalogues, when the game data names the value.
        value: The value as the host sent it.
    """

    name: str | None
    value: str


@dataclass(frozen=True, slots=True)
class Server:
    """A game that a server hosts, as the web pages show it.

    Attributes:
        slug: The end of its page's address, unique among the game's servers.
        name: Its name.
        players: How many players are in it.
        max_players: How many it takes.
        squad: How many players a squad of the game holds.
        since: When it was listed.
        map: The map it is played on, if the host says.
        mode: Its game mode, if the host says.
        expansion: The content it needs, if the host says.
        region: Where the server is, if the host says.
        hardcore: Whether it is played under hardcore rules.
    """

    slug: str
    name: str
    players: int
    max_players: int
    squad: int
    since: datetime
    map: Label | None
    mode: Label | None
    expansion: Label | None
    region: Label | None
    hardcore: bool

    @property
    def free(self) -> int:
        """How many more players it takes."""
        return max(self.max_players - self.players, 0)

    @property
    def room(self) -> bool:
        """Whether a whole squad fits."""
        return self.free >= self.squad


class Census:
    """Who plays the games now and where, as the web pages show it."""

    def __init__(self, hosting: Hosting, players: Players) -> None:
        """Count the players and the games that hosts list.

        Args:
            hosting: The games that hosts list.
            players: Who is logged in.
        """
        self._hosting = hosting
        self._players = players

    def activity(self, game: Game) -> Activity:
        """Return how many play a game now.

        Args:
            game: The game.
        """
        hosted = self._hosted(game)
        return Activity(
            online=len(self._players.playing_in(game)),
            in_matches=sum(len(entry.in_game()) for entry in hosted),
            servers=len(hosted),
        )

    def servers(self, game: Game) -> list[Server]:
        """Return the games that a game's servers host and players see, oldest first.

        Args:
            game: The game.
        """
        taken: set[str] = set()
        return [self._server(game, hosted, taken) for hosted in self._hosted(game)]

    def busiest(self, game: Game) -> Server | None:
        """Return the server with the most players that still has room for a squad, if any.

        Args:
            game: The game.
        """
        roomy = [server for server in self.servers(game) if server.room]
        return max(roomy, key=lambda server: server.players, default=None)

    def _hosted(self, game: Game) -> list[HostedGame]:
        hosted = (entry for entry in self._hosting.all_games(game) if entry.type == GAME)
        return sorted(hosted, key=lambda entry: entry.id)

    def _server(self, game: Game, hosted: HostedGame, taken: set[str]) -> Server:
        listing = game.listing
        attributes = {key.casefold(): value for key, value in hosted.basic.items()}
        return Server(
            slug=_unique(slug(hosted.name), taken),
            name=hosted.name,
            players=len(hosted.in_game()),
            max_players=hosted.max_players,
            squad=listing.squad,
            since=hosted.since,
            map=_label(listing.map, attributes),
            mode=_label(listing.mode, attributes),
            expansion=_label(listing.expansion, attributes),
            region=_label(listing.region, attributes),
            hardcore=_marked(listing.hardcore, attributes),
        )


def slug(name: str) -> str:
    """Return the end of the address of a server's page, made from its name.

    Args:
        name: The server's name.
    """
    return _NOT_IN_SLUGS.sub("-", name.casefold()).strip("-") or "server"


def _unique(wanted: str, taken: set[str]) -> str:
    # Servers that share a name get numbered addresses, oldest first.
    found = wanted
    number = 1
    while found in taken:
        number += 1
        found = f"{wanted}-{number}"
    taken.add(found)
    return found


def _label(names: Names | None, attributes: Mapping[str, str]) -> Label | None:
    # The attributes come with their keys folded, as hosts spell them in any case.
    value = attributes.get(names.attribute.casefold()) if names else None
    if names is None or value is None:
        return None
    folded = value.casefold()
    matching = [
        known
        for known in names.names
        if (folded.startswith(known.casefold()) if names.prefix else folded == known.casefold())
    ]
    if matching:
        return Label(names.names[max(matching, key=len)], value)
    return Label(names.otherwise, value)


def _marked(flag: Flag | None, attributes: Mapping[str, str]) -> bool:
    value = attributes.get(flag.attribute.casefold()) if flag else None
    return flag is not None and value is not None and value.casefold() == flag.value.casefold()
