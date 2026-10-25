import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import count

from openfesl.core.games import Flag, Game, Names, Roster
from openfesl.core.hosting import GAME, HostedGame, Hosting
from openfesl.core.session import Players

_NOT_IN_SLUGS = re.compile(r"[\W_]+")
# How many servers that stopped hosting keep their last known page.
ENDED_KEPT = 1000


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
class Seat:
    """A player in a hosted game, with what the server lists about the player's round.

    Attributes:
        name: The player's name.
        numbers: The round's numbers by what they count, such as score, as far as the game
            data knows what the server lists.
    """

    name: str
    numbers: Mapping[str, int] = field(default_factory=dict)


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
        roster: Its players, by score when the server lists one, otherwise in the order the
            server lists them.
        settings: Its settings that are on or off, by name, as far as the server publishes
            them.
        message: What the server's operator wrote for its players.
        ended: When it stopped hosting, for a server whose page shows what it was last.
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
    roster: tuple[Seat, ...]
    settings: tuple[tuple[str, bool], ...]
    message: str
    ended: datetime | None = None

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
        self._ended: dict[tuple[str, str], Server] = {}
        hosting.on_end(self._remember)

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

    def server(self, game: Game, wanted: str) -> Server | None:
        """Return a server by the end of its page's address, or what it was when it last hosted.

        Args:
            game: The game it belongs to.
            wanted: The end of the address.
        """
        live = next((server for server in self.servers(game) if server.slug == wanted), None)
        return live or self._ended.get((game.id, wanted))

    def in_menus(self, game: Game) -> list[str]:
        """Return the names of the players logged in to a game who are in no hosted game.

        Args:
            game: The game.
        """
        playing = {
            player.persona.id for hosted in self._hosted(game) for player in hosted.in_game()
        }
        return [
            persona.name for persona in self._players.playing_in(game) if persona.id not in playing
        ]

    def places(self, game: Game) -> dict[int, Server | None]:
        """Return where the players logged in to a game are, by persona.

        Args:
            game: The game.

        Returns:
            The server whose game each player is in, or None for a player in the menus.
        """
        found: dict[int, Server | None] = {
            persona.id: None for persona in self._players.playing_in(game)
        }
        taken: set[str] = set()
        for hosted in self._hosted(game):
            server = self._server(game, hosted, taken)
            for player in hosted.in_game():
                if player.persona.id in found:
                    found[player.persona.id] = server
        return found

    def _remember(self, hosted: HostedGame) -> None:
        if hosted.type != GAME or not hosted.listed:
            return
        game = hosted.host.game
        last = self._server(game, hosted, set())
        ended = replace(last, players=0, roster=(), ended=datetime.now(UTC))
        self._ended.pop((game.id, last.slug), None)
        self._ended[(game.id, last.slug)] = ended
        if len(self._ended) > ENDED_KEPT:
            del self._ended[next(iter(self._ended))]

    def _hosted(self, game: Game) -> list[HostedGame]:
        hosted = (entry for entry in self._hosting.all_games(game) if entry.type == GAME)
        return sorted(hosted, key=lambda entry: entry.id)

    def _server(self, game: Game, hosted: HostedGame, taken: set[str]) -> Server:
        listing = game.listing
        attributes = {
            key.casefold(): value for key, value in (hosted.basic | hosted.details).items()
        }
        playing = [player.persona.name for player in hosted.in_game()]
        roster = _seats(listing.roster, attributes, playing)
        return Server(
            slug=_unique(slug(hosted.name), taken),
            name=hosted.name,
            players=len(roster),
            max_players=hosted.max_players,
            squad=listing.squad,
            since=hosted.since,
            map=_label(listing.map, attributes),
            mode=_label(listing.mode, attributes),
            expansion=_label(listing.expansion, attributes),
            region=_label(listing.region, attributes),
            hardcore=_marked(listing.hardcore, attributes),
            roster=roster,
            settings=tuple(
                (name, _switched(attributes[attribute.casefold()]))
                for name, attribute in listing.settings.items()
                if attribute.casefold() in attributes
            ),
            message=_message(listing.message, attributes),
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


def _switched(value: str) -> bool:
    # Settings come as 1 or 0, a share such as 0.5000, or a word.
    try:
        return float(value) > 0
    except ValueError:
        return value.casefold() in {"yes", "true", "on"}


def _message(name: str | None, attributes: Mapping[str, str]) -> str:
    if name is None:
        return ""
    declared = attributes.get(f"{name}Count".casefold(), "")
    pieces: list[str] = []
    for index in range(int(declared)) if declared.isdecimal() else count():
        piece = attributes.get(f"{name}{index}".casefold())
        if piece is None:
            break
        pieces.append(piece)
    return "".join(pieces)


def _seats(
    roster: Roster | None, attributes: Mapping[str, str], playing: list[str]
) -> tuple[Seat, ...]:
    # The server's list may lag behind who is in the game; the game's own players are the ones
    # shown, with what the server lists about them.
    listed = _listed(roster, attributes) if roster else {}
    order = {name: index for index, name in enumerate(listed)}
    seats = sorted(
        (Seat(name, listed.get(name.casefold(), {})) for name in playing),
        key=lambda seat: order.get(seat.name.casefold(), len(order)),
    )
    if roster and "score" in roster.fields:
        seats.sort(key=lambda seat: -seat.numbers.get("score", 0))
    return tuple(seats)


def _listed(roster: Roster, attributes: Mapping[str, str]) -> dict[str, dict[str, int]]:
    # The attributes are numbered with two digits; empty player slots come without a name.
    listed: dict[str, dict[str, int]] = {}
    for index in range(100):
        value = attributes.get(f"{roster.attribute}{index:02d}".casefold())
        if value is None:
            break
        fields = list(zip(roster.fields, value.split(roster.separator), strict=False))
        name = next((text for meaning, text in fields if meaning == "name"), "")
        if name:
            listed.setdefault(
                name.casefold(),
                {
                    meaning: int(text)
                    for meaning, text in fields
                    if meaning not in ("name", "") and text.isdecimal()
                },
            )
    return listed
