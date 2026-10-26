import logging
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from openfesl.core.fesl import SYSTEM_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Games
from openfesl.core.launcher import LauncherSessions
from openfesl.core.session import Players
from openfesl.protocol.acct import GetLockerURLAnswer
from openfesl.protocol.locker import NOT_LOGGED_IN, empty_locker, locker_error
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)

XML = "text/xml"


@dataclass(frozen=True, slots=True)
class HttpAnswer:
    """What one of the game's HTTP requests is answered with.

    Attributes:
        status: The HTTP status.
        body: The body.
        media_type: The body's media type.
    """

    status: int
    body: bytes = b""
    media_type: str = "text/plain"


NOT_FOUND = HttpAnswer(404)
METHOD_NOT_ALLOWED = HttpAnswer(405)
NOT_IMPLEMENTED = HttpAnswer(501)


class GameHttp:
    """The requests that games send to EA's web servers: editorial files, the locker and Nucleus.

    A request is found by the host the game asked for and its path. The files are rendered once,
    since neither they nor the news change while the server runs.
    """

    def __init__(
        self, games: Games, players: Players, news: dict[str, str], sessions: LauncherSessions
    ) -> None:
        """Render every game's editorial files and note where their lockers and Nucleus hosts are.

        Args:
            games: The games the server serves.
            players: The players' sessions, which the locker's session keys belong to.
            news: The news text by language code.
            sessions: The launcher sign-ins, from which a Nucleus token request is answered.

        Raises:
            KeyError: When a file names a placeholder that no news text fills.
            ValueError: When two games claim the same file.
        """
        self._players = players
        self._sessions = sessions
        placeholders = {f"news_{language}": escape(text) for language, text in news.items()}
        self._files: dict[tuple[str, str], HttpAnswer] = {}
        self._lockers: set[tuple[str, str]] = set()
        self._nucleus: set[str] = set()
        for game in games:
            if game.http.locker:
                self._lockers.add(_place(game.http.locker))
            if game.http.nucleus:
                self._nucleus.add(game.http.nucleus.lower())
            for url, template in game.http.files.items():
                if _place(url) in self._files:
                    raise ValueError(f"{url} belongs to two games")
                body = Template(template).substitute(placeholders).strip().encode()
                media_type = mimetypes.guess_type(url)[0] or "text/plain"
                self._files[_place(url)] = HttpAnswer(200, body, media_type)

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handler of the transaction that tells the game where its locker is."""
        return {("acct", "GetLockerURL"): Route(Message, self.locker_url)}

    async def locker_url(self, connection: FeslConnection, request: Message) -> GetLockerURLAnswer:
        """Return the URL of the game's locker.

        Args:
            connection: The FESL connection the request came on.
            request: The request, which has no fields.

        Raises:
            TransactionError: When the game has no locker.
        """
        assert connection.game is not None
        if connection.game.http.locker is None:
            raise TransactionError(SYSTEM_ERROR)
        return GetLockerURLAnswer(url=connection.game.http.locker)

    def answer(
        self,
        method: str,
        host: str,
        path: str,
        query: Mapping[str, str],
        cookies: Mapping[str, str],
    ) -> HttpAnswer:
        """Answer one of the game's HTTP requests.

        Args:
            method: The request's method.
            host: The host the game asked for, with or without a port.
            path: The request's path.
            query: The request's query parameters.
            cookies: The request's cookies, which carry the launcher's session id to Nucleus.
        """
        place = (host.split(":")[0].lower(), path)
        nucleus = self._nucleus_answer(path, query, cookies) if place[0] in self._nucleus else None
        if place in self._lockers:
            answer = self._locker(query)
        elif nucleus is not None:
            answer = nucleus
        elif (found := self._files.get(place)) is None:
            answer = NOT_FOUND
        else:
            answer = found if method in {"GET", "HEAD"} else METHOD_NOT_ALLOWED
        logger.log(
            logging.DEBUG if answer.status < 400 else logging.INFO,
            "Game HTTP %s %s%s: %d",
            method,
            *place,
            answer.status,
        )
        return answer

    def _locker(self, query: Mapping[str, str]) -> HttpAnswer:
        session = self._players.find_key(query.get("lkey", ""))
        if session is None or session.persona is None:
            return HttpAnswer(200, locker_error(NOT_LOGGED_IN), XML)
        # Nothing is stored, so only the listing of an empty locker has an answer.
        if query.get("cmd") != "dir":
            return NOT_IMPLEMENTED
        persona = session.persona
        listing = empty_locker(query.get("game", ""), persona.id, query.get("pers", persona.name))
        return HttpAnswer(200, listing, XML)

    def _nucleus_answer(
        self, path: str, query: Mapping[str, str], cookies: Mapping[str, str]
    ) -> HttpAnswer | None:
        if path == "/nucleus/authToken":
            return self._auth_token(query, cookies)
        if path in {"/dc/submit", "/dc/submitTelemetry"}:
            # Telemetry the game can do without; the <success> root accepts it, since a Magma result
            # the game does not recognise as a success or an error is a denial it retries.
            return HttpAnswer(200, b"<success/>", XML)
        return None

    def _auth_token(self, query: Mapping[str, str], cookies: Mapping[str, str]) -> HttpAnswer:
        # The game carries the launcher's session id as a magma cookie (older builds as a query
        # parameter). A Magma result's root tells the game its fate: <success> holding the <token>
        # the game reads, or <error> for a failure it retries; any other root it reads as a denial.
        session = self._sessions.find(cookies.get("magma") or query.get("magma", ""))
        if session is None:
            return HttpAnswer(200, b'<error code="21">no such session</error>', XML)
        body = f"<success><token>{escape(session.token)}</token></success>".encode()
        return HttpAnswer(200, body, XML)


def _place(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return (parts.hostname or "", parts.path)
