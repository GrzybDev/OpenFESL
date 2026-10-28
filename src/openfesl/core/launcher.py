import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from openfesl.core.accounts import STATE_ERRORS
from openfesl.core.fesl import TransactionError
from openfesl.core.games import Games
from openfesl.core.logins import Logins
from openfesl.core.personas import Persona, PersonaService
from openfesl.core.session import GameSession, Players

logger = logging.getLogger(__name__)

# A game names its launcher session in every Magma request, a dedicated server's for days on end,
# and fetches its token again whenever it logs in anew, such as after losing its connection. So a
# session is kept while its account is logged in to a game, and for this long past its last use.
SESSION_TTL = 24 * 3600.0


class Refusal(StrEnum):
    """Why a launcher sign-in was refused, which the launcher words for the player."""

    UNKNOWN_GAME = "unknown-game"
    WRONG_SIGN_IN = "wrong-sign-in"
    DISABLED = "disabled"
    BANNED = "banned"
    DEACTIVATED = "deactivated"
    NO_PERSONA = "no-persona"


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
    """

    token: str
    account_id: int
    account_name: str
    persona_name: str


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

    def find(self, session_id: str) -> LauncherSession | None:
        """Return the sign-in a session id names while it is kept, which this use extends.

        Args:
            session_id: The id that the game carries in its Magma requests.
        """
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
        self, logins: Logins, games: Games, personas: PersonaService, sessions: LauncherSessions
    ) -> None:
        """Sign players in against the account store.

        Args:
            logins: Checks passwords and hands out login tokens.
            games: The games the server serves, to refuse a launcher for one it does not.
            personas: Where the account's persona is found or made, so the game logs one in.
            sessions: Where the sign-in is kept for the game to pick up.
        """
        self._logins = logins
        self._games = games
        self._personas = personas
        self._sessions = sessions

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
                account's state refuses its logins or no persona could be made for it.
        """
        served = self._games.by_id(game)
        if served is None:
            raise LauncherError(Refusal.UNKNOWN_GAME)
        try:
            credentials = await self._logins.check(account, password)
            assert credentials.password_hash is not None
            token = await self._logins.remember(credentials.account.id, credentials.password_hash)
        except TransactionError as refused:
            raise LauncherError(STATE_REFUSALS.get(refused.code, Refusal.WRONG_SIGN_IN)) from None
        persona = await self._personas.first_or_new(credentials.account, served.namespace)
        if persona is None:
            raise LauncherError(Refusal.NO_PERSONA)
        session = LauncherSession(
            token=token,
            account_id=credentials.account.id,
            account_name=credentials.account.nuid,
            persona_name=persona.name,
        )
        session_id = self._sessions.issue(session)
        logger.info("Launcher signed account %d in to %s to %s", credentials.account.id, game, role)
        return session_id, session
