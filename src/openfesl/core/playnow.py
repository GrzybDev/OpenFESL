import asyncio
import itertools
import logging
from typing import Any

from openfesl.core.fesl import FeslConnection, Route, TransactionError
from openfesl.core.games import Game
from openfesl.core.hosting import GAME, HostedGame, Hosting
from openfesl.core.personas import NOT_LOGGED_IN
from openfesl.protocol.message import Message
from openfesl.protocol.pnow import (
    Cancel,
    FoundGame,
    Outcome,
    SessionId,
    Start,
    StartAnswer,
    Status,
)

logger = logging.getLogger(__name__)

COMPLETE = "COMPLETE"

FIND_SERVER = "findServer"
LIST_SERVERS = "listServers"

JOIN = "JOIN"
LIST = "LIST"
NO_MATCH = "NOMATCH"
NO_SERVER = "NOSERVER"

CLOSED = "C"

# The game looks the games up one after another and joins the first it finds, so a few
# spare ones cover games that end meanwhile.
JOIN_CHOICES = 5
LIST_LENGTH = 20


class PlayNow:
    """PlayNow: the hosted games a player can join now, best fit first."""

    def __init__(self, hosting: Hosting) -> None:
        """Search the games hosts list.

        Args:
            hosting: The games hosts list.
        """
        self._hosting = hosting
        self._ids = itertools.count(1)

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the PlayNow transactions."""
        return {
            ("pnow", "Start"): Route(Start, self.start),
            ("pnow", "Cancel"): Route(Cancel, self.cancel),
        }

    async def start(self, connection: FeslConnection, request: Start) -> StartAnswer:
        """Answer Start with the search's id, then push its Status with the games it found.

        Args:
            connection: The player's FESL connection.
            request: The partition and the player's filters and preferences.

        Raises:
            TransactionError: When nobody plays on the connection.
        """
        if connection.session.persona is None or connection.game is None:
            raise TransactionError(NOT_LOGGED_IN)
        props = request.players[0].props if request.players else {}
        search = SessionId(id=next(self._ids), partition=request.partition.partition)
        status = Status(
            id=search, session_state=COMPLETE, props=self._search(connection.game, props)
        )
        logger.info("PlayNow %d found %s", search.id, status.props.result_type)
        # The game takes a Status only for the search its answer named, in that partition.
        asyncio.get_running_loop().call_soon(connection.push, "pnow", "Status", status)
        return StartAnswer(id=search)

    async def cancel(self, connection: FeslConnection, request: Cancel) -> Message:
        """Answer Cancel; a search is complete by the time the player can stop it.

        Args:
            connection: The player's FESL connection.
            request: The search.
        """
        return Message()

    def _search(self, game: Game, props: dict[str, str]) -> Outcome:
        kind = props.get("sessionType", FIND_SERVER)
        listed = [hosted for hosted in self._hosting.all_games(game) if hosted.type == GAME]
        # The server finds games; it never makes a player's game a host.
        if kind not in {FIND_SERVER, LIST_SERVERS} or not listed:
            return Outcome(result_type=NO_SERVER, available_server_count=0)
        fits = [
            (_fit(props, hosted), hosted)
            for hosted in listed
            if _joinable(hosted) and _passes(props, hosted)
        ]
        if not fits:
            return Outcome(result_type=NO_MATCH, available_server_count=0)
        # The best fit first, then the fuller game, so players end up together.
        fits.sort(key=lambda found: (-found[0], -found[1].taken(), found[1].id))
        if kind == LIST_SERVERS:
            wanted = props.get("maxListServersResult", "")
            length = int(wanted) if wanted.isdigit() and int(wanted) > 0 else LIST_LENGTH
        else:
            length = JOIN_CHOICES
        games = [
            FoundGame(lid=hosted.lobby.id, gid=hosted.id, fit=fit) for fit, hosted in fits[:length]
        ]
        return Outcome(
            result_type=LIST if kind == LIST_SERVERS else JOIN,
            games=games,
            avg_fit=sum(found.fit for found in games) / len(games),
            available_server_count=len(fits),
        )


def _joinable(hosted: HostedGame) -> bool:
    # PlayNow has no way to ask for a password.
    return hosted.join != CLOSED and not hosted.password and hosted.taken() < hosted.max_players


def _value(hosted: HostedGame, target: str) -> str | None:
    # `version` is the game protocol version, `U-<key>` the basic attribute `B-U-<key>`.
    if target == "version":
        return hosted.version
    attributes = {key.casefold(): value for key, value in hosted.basic.items()}
    return attributes.get(f"B-{target}".casefold())


def _rules(props: dict[str, str], kind: str) -> list[tuple[str, list[str]]]:
    rules = []
    for key, value in props.items():
        name = key.removeprefix(f"{kind}-")
        target = props.get(f"{kind}ToGame-{name}")
        if name != key and target and value:
            rules.append((target, value.split("|")))
    return rules


def _passes(props: dict[str, str], hosted: HostedGame) -> bool:
    return all(_value(hosted, target) in allowed for target, allowed in _rules(props, "filter"))


def _fit(props: dict[str, str], hosted: HostedGame) -> float:
    preferences = _rules(props, "pref")
    if not preferences:
        return 1.0
    met = sum(_value(hosted, target) in wanted for target, wanted in preferences)
    return met / len(preferences)
