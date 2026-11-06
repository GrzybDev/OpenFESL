import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from openfesl.core.accounts import STATE_ERRORS, Account
from openfesl.core.fesl import TransactionError
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Heroes
from openfesl.core.logins import SERVER_TOKEN, Logins
from openfesl.core.personas import Persona, PersonaService
from openfesl.core.session import GameSession, Players
from openfesl.core.start_links import StartLinks

logger = logging.getLogger(__name__)

# A game names its launcher session in every Magma request, a dedicated server's for days on end,
# and fetches its token again whenever it logs in anew, such as after losing its connection. So a
# session is kept while its account is logged in to a game, and for this long past its last use.
SESSION_TTL = 24 * 3600.0

HOST = "host"


class Refusal(StrEnum):
    """Why a launcher sign-in was refused, which the launcher words for the player."""

    UNKNOWN_GAME = "unknown-game"
    WRONG_SIGN_IN = "wrong-sign-in"
    DISABLED = "disabled"
    BANNED = "banned"
    DEACTIVATED = "deactivated"
    NO_PERSONA = "no-persona"
    NO_HERO = "no-hero"


# The account states that refuse a login, by the NuLogin error each refuses it with.
STATE_REFUSALS = {code: Refusal(state.value) for state, code in STATE_ERRORS.items()}


class LauncherError(Exception):
    """Refuses a launcher sign-in."""

    def __init__(self, refusal: Refusal) -> None:
        """Refuse a sign-in for a reason.

        Args:
            refusal: Why it was refused.
        """
        super().__init__(refusal)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class LauncherSession:
    """What a launcher sign-in holds for the game it starts, which names it in its Magma requests.

    Attributes:
        token: The login token the game logs in with, which nucleus/authToken hands it.
        account_id: The account that signed in.
        account_name: The account's login name.
        persona_name: The persona the game plays or hosts as.
        server: Whether it is the session that a game's dedicated servers share.
    """

    token: str
    account_id: int
    account_name: str
    persona_name: str
    server: bool = False


class LauncherSessions:
    """The sign-ins the launcher made, kept by session id while the games they start use them."""

    def __init__(
        self,
        players: Players,
        ttl: float = SESSION_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Keep sign-ins while their accounts are logged in and for a while past their last use.

        Args:
            players: The game sessions that accounts are logged in on.
            ttl: How long a session id outlives its last use once its account is logged in to no
                game, in seconds.
            clock: Reads the current time in seconds, for the expiry.
        """
        self._players = players
        self._ttl = ttl
        self._clock = clock
        self._sessions: dict[str, tuple[LauncherSession, float]] = {}
        self._servers: dict[str, LauncherSession] = {}
        players.on_leave(self._left)

    def issue(self, session: LauncherSession) -> str:
        """Keep a sign-in under a new session id and return the id.

        Args:
            session: The sign-in to keep.
        """
        self._purge()
        session_id = secrets.token_urlsafe(24)
        self._sessions[session_id] = (session, self._clock() + self._ttl)
        return session_id

    def keep_server(self, session_id: str, session: LauncherSession) -> None:
        """Keep the session of a game's dedicated servers, under an id that outlives a restart.

        Args:
            session_id: The id.
            session: The session.
        """
        self._servers[session_id] = session

    def find(self, session_id: str) -> LauncherSession | None:
        """Return the sign-in a session id names while it is kept, which this use extends.

        Args:
            session_id: The id that the game carries in its Magma requests.
        """
        if server := self._servers.get(session_id):
            return server
        kept = self._sessions.get(session_id)
        if kept is None:
            return None
        session, expiry = kept
        now = self._clock()
        if self._expired(session, expiry, now):
            del self._sessions[session_id]
            return None
        self._sessions[session_id] = (session, now + self._ttl)
        return session

    def _purge(self) -> None:
        now = self._clock()
        for session_id in [
            name
            for name, (session, expiry) in self._sessions.items()
            if self._expired(session, expiry, now)
        ]:
            del self._sessions[session_id]

    def _left(self, session: GameSession, persona: Persona) -> None:
        # A game that lost its connection fetches its token again to log in anew, so the end of its
        # last game counts as a use, however long ago it last asked for anything.
        expiry = self._clock() + self._ttl
        for session_id, (kept, _) in list(self._sessions.items()):
            if kept.account_id == persona.account_id:
                self._sessions[session_id] = (kept, expiry)

    def _expired(self, session: LauncherSession, expiry: float, now: float) -> bool:
        return now >= expiry and self._players.online(session.account_id) is None


class LauncherService:
    """Signs a player in for a launcher and starts a session the game then logs in from."""

    def __init__(
        self,
        logins: Logins,
        games: Games,
        personas: PersonaService,
        heroes: Heroes,
        sessions: LauncherSessions,
        links: StartLinks,
    ) -> None:
        """Sign players in against the account store.

        Args:
            logins: Checks passwords and hands out login tokens.
            games: The games the server serves, to refuse a launcher for one it does not.
            personas: Where the account's persona is found or made, so the game logs one in.
            heroes: The heroes of the games whose heroes are made on their site, of which the
                game logs in the default one.
            sessions: Where the sign-in is kept for the game to pick up.
            links: The start links the site made, which sign their accounts in once.
        """
        self._logins = logins
        self._games = games
        self._personas = personas
        self._heroes = heroes
        self._sessions = sessions
        self._links = links

    async def prepare(self, servers: list[tuple[Game, Account]]) -> None:
        """Keep the session of each game's dedicated servers that start through a launcher.

        Args:
            servers: Each game with its dedicated servers' account.
        """
        for game, account in servers:
            persona = await self._personas.first_or_new(account, game.namespace)
            if persona is None:
                continue
            session = LauncherSession(
                SERVER_TOKEN, account.id, account.nuid, persona.name, server=True
            )
            self._sessions.keep_server(_server_session_id(game), session)

    def host(self, game: str) -> tuple[str, LauncherSession]:
        """Return the session that a game's dedicated servers share, which needs no account.

        Args:
            game: The game's data-file name.

        Raises:
            LauncherError: When the game is unknown or has no such session.
        """
        session_id = _server_session_id(self._served(game))
        session = self._sessions.find(session_id)
        if session is None:
            raise LauncherError(Refusal.NO_PERSONA)
        return session_id, session

    async def sign_in(
        self, game: str, role: str, account: str, password: str
    ) -> tuple[str, LauncherSession]:
        """Check an account's password and start a launcher session for a game.

        Args:
            game: The game's data-file name.
            role: Whether the game is started to play or to host.
            account: The account's login name.
            password: The account's password.

        Returns:
            The session id to launch the game with, and the session it names.

        Raises:
            LauncherError: When the game is unknown, the account or password is wrong, the
                account's state refuses its logins, no persona could be made for it or, in a
                game whose heroes are made on its site, it has no hero to start the game as.
        """
        served = self._served(game)
        try:
            credentials = await self._logins.check(account, password)
            token = await self._logins.remember(credentials.account.id, credentials.password_hash)
        except TransactionError as refused:
            raise LauncherError(STATE_REFUSALS.get(refused.code, Refusal.WRONG_SIGN_IN)) from None
        return await self._start(credentials.account, served, role, token)

    async def redeem(self, game: str, role: str, code: str) -> tuple[str, LauncherSession]:
        """Trade a start link's code for a launcher session that plays a game.

        Args:
            game: The game's data-file name.
            role: Whether the game is started to play or to host; a start link only plays.
            code: The start link's code, which works once.

        Returns:
            The session id to launch the game with, and the session it names.

        Raises:
            LauncherError: When the game is unknown; the code is unknown, used, ended or made
                for another game, or the role hosts; the account's state refuses its logins; or
                it has no hero to play as.
        """
        served = self._served(game)
        link = self._links.take(code, game) if role != HOST else None
        if link is None:
            raise LauncherError(Refusal.WRONG_SIGN_IN)
        try:
            account, token = await self._logins.vouch(link.account_id, link.epoch)
        except TransactionError as refused:
            raise LauncherError(STATE_REFUSALS.get(refused.code, Refusal.WRONG_SIGN_IN)) from None
        session_id, session = await self._start(account, served, role, token)
        self._links.signed_in(link, session.persona_name)
        return session_id, session

    def _served(self, game: str) -> Game:
        served = self._games.by_id(game)
        if served is None:
            raise LauncherError(Refusal.UNKNOWN_GAME)
        return served

    async def _start(
        self, account: Account, game: Game, role: str, token: str
    ) -> tuple[str, LauncherSession]:
        persona = await self._persona(account, game)
        if persona is None:
            raise LauncherError(Refusal.NO_HERO if game.heroes else Refusal.NO_PERSONA)
        session = LauncherSession(
            token=token, account_id=account.id, account_name=account.nuid, persona_name=persona.name
        )
        session_id = self._sessions.issue(session)
        logger.info("Launcher signed account %d in to %s to %s", account.id, game.id, role)
        return session_id, session

    async def _persona(self, account: Account, game: Game) -> Persona | None:
        # A game whose heroes are made on its site is given none. It plays as the default hero,
        # or else the oldest, of those it can play.
        if not game.heroes:
            return await self._personas.first_or_new(account, game.namespace)
        hero = (await self._heroes.roster(game, account.id)).playing
        return hero.persona if hero else None


def _server_session_id(game: Game) -> str:
    return f"server.{game.id}"
