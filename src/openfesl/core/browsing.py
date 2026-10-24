import logging
from dataclasses import dataclass
from typing import Any, Self

from openfesl.core.hosting import GAME, NO_GAME, HostedGame, Hosting, Player, PlayerState
from openfesl.core.theater import FAILED, TheaterConnection, TheaterError, TheaterRoute
from openfesl.protocol.theater import (
    GameData,
    GameDetails,
    GameFilter,
    GameList,
    GameListAnswer,
    GameLookup,
    LobbyData,
    LobbyListAnswer,
    PlayerCount,
    PlayerCountAnswer,
    PlayerData,
)

logger = logging.getLogger(__name__)

CLOSED = "C"

_FILTER = "FILTER-ATTR-"


@dataclass(frozen=True, slots=True)
class Favourites:
    """The players and games a player marked, as a list request names them.

    Attributes:
        names: Persona names, folded for comparison.
        user_ids: Persona ids.
        games: Game names.
        game_ids: Persistent game ids (UGIDs).
    """

    names: frozenset[str] = frozenset()
    user_ids: frozenset[int] = frozenset()
    games: frozenset[str] = frozenset()
    game_ids: frozenset[str] = frozenset()

    @classmethod
    def of(cls, request: GameFilter) -> Self:
        """Read the favourites of a list request.

        Args:
            request: The request.
        """
        return cls(
            names=frozenset(name.casefold() for name in _split(request.fav_player)),
            user_ids=frozenset(int(uid) for uid in _split(request.fav_player_uid) if uid.isdigit()),
            games=frozenset(_split(request.fav_game)),
            game_ids=frozenset(_split(request.fav_game_uid)),
        )

    def marks(self, hosted: HostedGame) -> bool:
        """Return whether a game is a favourite.

        Args:
            hosted: The game.
        """
        return hosted.name in self.games or hosted.ugid in self.game_ids

    def players_in(self, hosted: HostedGame) -> int:
        """Return how many favourite players play in a game.

        Args:
            hosted: The game.
        """
        return sum(
            player.persona.id in self.user_ids or player.persona.name.casefold() in self.names
            for player in hosted.in_game()
        )


class Browsing:
    """The lobby and game lists players browse, and the lookups of single games."""

    def __init__(self, hosting: Hosting) -> None:
        """Show the games hosts list.

        Args:
            hosting: The games hosts list.
        """
        self._hosting = hosting

    def routes(self) -> dict[str, TheaterRoute[Any]]:
        """Return the handlers of the commands that list and count lobbies, games and players."""
        return {
            "LLST": TheaterRoute(GameFilter, self.list_lobbies),
            "GLST": TheaterRoute(GameList, self.list_games),
            "GDAT": TheaterRoute(GameLookup, self.look_up),
            "PCNT": TheaterRoute(PlayerCount, self.count_players),
        }

    async def list_lobbies(
        self, connection: TheaterConnection, request: GameFilter, tid: int
    ) -> None:
        """Answer LLST with the game's lobbies, one LDAT row each after the answer.

        Args:
            connection: The connection the request came on.
            request: The filters the counts of passing and favourite games follow.
            tid: The request's TID, which the rows carry too.
        """
        lobbies = connection.game.lobbies
        favourites = Favourites.of(request)
        connection.send("LLST", LobbyListAnswer(num_lobbies=len(lobbies)), tid)
        for lobby in lobbies:
            # Lobbies hold games; playgroups do not fill them.
            games = [
                hosted
                for hosted in self._hosting.games(connection.game, lobby)
                if hosted.type == GAME
            ]
            row = LobbyData(
                lid=lobby.id,
                passing=sum(_passes(request, hosted, favourites) for hosted in games),
                name=lobby.name,
                locale=lobby.locale,
                max_games=lobby.max_games,
                favorite_games=sum(favourites.marks(hosted) for hosted in games),
                favorite_players=sum(favourites.players_in(hosted) for hosted in games),
                num_games=len(games),
            )
            await connection.send_row("LDAT", row, tid)

    async def list_games(self, connection: TheaterConnection, request: GameList, tid: int) -> None:
        """Answer GLST with how many games pass, then one GDAT row each after the answer.

        Theater has no fragments, so every game travels in a packet of its own.

        Args:
            connection: The connection the request came on.
            request: The lobby, the filters and the range of games.
            tid: The request's TID, which the rows carry too.

        Raises:
            TheaterError: When the game has no such lobby.
        """
        lobby = next((lobby for lobby in connection.game.lobbies if lobby.id == request.lid), None)
        if lobby is None:
            raise TheaterError(FAILED)
        favourites = Favourites.of(request)
        games = sorted(self._hosting.games(connection.game, lobby), key=lambda hosted: hosted.id)
        listed = [
            hosted
            for hosted in games
            if hosted.id >= request.gid
            and request.type in {None, hosted.type}
            and _passes(request, hosted, favourites)
        ]
        # A COUNT of 0 or below asks for every game.
        if request.count > 0:
            listed = listed[: request.count]
        answer = GameListAnswer(
            num_games=len(listed),
            lid=lobby.id,
            lobby_max_games=lobby.max_games,
            lobby_num_games=self._hosting.count(connection.game, lobby),
        )
        connection.send("GLST", answer, tid)
        for hosted in listed:
            await connection.send_row("GDAT", _game_data(hosted, favourites), tid)

    async def count_players(
        self, connection: TheaterConnection, request: PlayerCount, tid: int
    ) -> None:
        """Answer PCNT with how many players the games of a lobby, or of every lobby, have.

        Args:
            connection: The connection the request came on.
            request: The lobby, or -1 for all of them.
            tid: The request's TID.
        """
        game = connection.game
        lobbies = [lobby for lobby in game.lobbies if request.lid in {-1, lobby.id}]
        count = sum(
            len(hosted.in_game())
            for lobby in lobbies
            for hosted in self._hosting.games(game, lobby)
        )
        connection.send("PCNT", PlayerCountAnswer(count=count, lid=request.lid), tid)

    async def look_up(self, connection: TheaterConnection, request: GameLookup, tid: int) -> None:
        """Answer a GDAT lookup with the game, then its GDET and a PDAT row per player in it.

        Args:
            connection: The connection the request came on.
            request: The game, or a player in it.
            tid: The request's TID, which the rows carry too.

        Raises:
            TheaterError: When no such game is listed.
        """
        hosted: HostedGame | None = None
        if request.lid is not None and request.gid is not None:
            hosted = self._hosting.find(connection.game, request.lid, request.gid)
        elif request.user or request.uid is not None:
            found = self._hosting.find_by_player(
                connection.game, request.type, request.user, request.uid
            )
            hosted = found[0] if found else None
        if hosted is None:
            raise TheaterError(NO_GAME)
        connection.send("GDAT", _game_data(hosted, Favourites()), tid)
        details = GameDetails(
            lid=hosted.lobby.id, gid=hosted.id, ugid=hosted.ugid, attributes=hosted.details
        )
        await connection.send_row("GDET", details, tid)
        for player in hosted.in_game():
            await connection.send_row("PDAT", _player_data(hosted, player), tid)


def _passes(request: GameFilter, hosted: HostedGame, favourites: Favourites) -> bool:
    # A game without a filtered attribute fails the filter, as in the SDK's own LAN lists; an
    # empty filter value asks for nothing.
    attributes = {key.casefold(): value for key, value in hosted.basic.items()}
    return (
        not (request.filter_not_full and hosted.taken() >= hosted.max_players)
        and not (request.filter_not_private and hosted.password)
        and not (request.filter_not_closed and hosted.join == CLOSED)
        and len(hosted.in_game()) >= request.filter_min_size
        and all(
            attributes.get(f"B-{key[len(_FILTER) :]}".casefold()) == value
            for key, value in request.attributes.items()
            if value
        )
        and not (
            request.filter_fav_only
            and not favourites.marks(hosted)
            and not favourites.players_in(hosted)
        )
    )


def _game_data(hosted: HostedGame, favourites: Favourites) -> GameData:
    # Only what lists show: never the game's secret, key or the host's LAN address.
    host = hosted.host.persona
    assert host is not None
    return GameData(
        lid=hosted.lobby.id,
        gid=hosted.id,
        n=hosted.name,
        type=hosted.type,
        i=str(hosted.address),
        p=hosted.port,
        pw=hosted.password,
        mp=hosted.max_players,
        ap=len(hosted.in_game()),
        jp=sum(player.state is not PlayerState.ENTERED for player in hosted.players.values()),
        qp=len(hosted.queue),
        hn=host.name,
        hu=host.id,
        v=hosted.host.version,
        pl=hosted.host.platform,
        j=hosted.join,
        f=favourites.marks(hosted),
        nf=favourites.players_in(hosted),
        max_observers=hosted.max_observers,
        num_observers=hosted.observers,
        version=hosted.version,
        attributes=hosted.basic,
    )


def _player_data(hosted: HostedGame, player: Player) -> PlayerData:
    return PlayerData(
        lid=hosted.lobby.id,
        gid=hosted.id,
        pid=player.pid,
        name=player.persona.name,
        uid=player.persona.id,
        attributes=player.attributes,
    )


def _split(names: str) -> list[str]:
    return [name for name in names.split(";") if name]
