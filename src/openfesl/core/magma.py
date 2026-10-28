import logging

from openfesl.core.launcher import LauncherSession, LauncherSessions

logger = logging.getLogger(__name__)

# The session id of a game started without one, which it still sends as its magma cookie.
NO_SESSION_ID = "0"

# The code and causes of a request that names no session. The game only logs them, as "code -
# cause"; the original service's own for this case are not known.
NOT_LOGGED_IN = "NOT_LOGGED_IN"
NO_SESSION = "NO_SESSION"
UNKNOWN_SESSION = "UNKNOWN_SESSION"


class MagmaError(Exception):
    """Refuses a Magma request with the code and cause of the error document it is answered with."""

    def __init__(self, code: str, cause: str) -> None:
        """Refuse a request.

        Args:
            code: What failed.
            cause: Why it failed.
        """
        super().__init__(f"{code} - {cause}")
        self.code = code
        self.cause = cause


class Magma:
    """Finds who asks Magma: the launcher session that a request's magma cookie names."""

    def __init__(self, sessions: LauncherSessions) -> None:
        """Find the askers among the launcher's sign-ins.

        Args:
            sessions: The launcher's sign-ins, by session id.
        """
        self._sessions = sessions

    def session(self, session_id: str | None) -> LauncherSession:
        """Return the launcher session that a request's magma cookie names.

        Args:
            session_id: The cookie's value, the session id the game was started with; none when
                the request carries no cookie.

        Raises:
            MagmaError: When the request names no session, or one that is unknown or expired.
        """
        if not session_id or session_id == NO_SESSION_ID:
            raise MagmaError(NOT_LOGGED_IN, NO_SESSION)
        session = self._sessions.find(session_id)
        if session is None:
            logger.info("A Magma request named an unknown launcher session")
            raise MagmaError(NOT_LOGGED_IN, UNKNOWN_SESSION)
        return session
