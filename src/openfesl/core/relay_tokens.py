from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openfesl.core.session import GameSession


class RelayTokens:
    """The relay sessions by the secret token that each one's WebSocket was answered with.

    The hook sends its WebSocket's token with every HTTP request of the game process while the
    WebSocket is open, so a request without a session cookie, such as a dedicated server's call
    to Magma, still tells which game process sent it. Tokens are secrets that nothing logs.
    """

    def __init__(self) -> None:
        """Start with no relay session known by a token."""
        self._sessions: dict[str, GameSession] = {}

    def add(self, token: str, session: GameSession) -> None:
        """Know a relay session by the token its WebSocket was answered with.

        Args:
            token: The token.
            session: The relay session.
        """
        self._sessions[token] = session

    def find(self, token: str) -> GameSession | None:
        """Return the relay session that a token was given to, while its WebSocket is open.

        Args:
            token: The token a request carried.
        """
        return self._sessions.get(token)

    def forget(self, session: GameSession) -> None:
        """Stop knowing a relay session by its token, as when its WebSocket closed.

        Args:
            session: The relay session.
        """
        for token in [token for token, known in self._sessions.items() if known is session]:
            del self._sessions[token]
