import hmac
import logging
from collections.abc import Mapping
from typing import Any

from openfesl.core.accounts import Account
from openfesl.core.connection import Address, Connection, Outbox
from openfesl.core.fesl import FeslConnection, Route
from openfesl.core.personas import Persona
from openfesl.protocol.message import Message
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Channel, Open

logger = logging.getLogger(__name__)

LOGGED_IN_ELSEWHERE = 2


class Players:
    """The sessions that players are logged in on, so that an account plays in one place.

    The personas they play as are known too, so that what other players do reaches them.
    """

    def __init__(self) -> None:
        """Start with nobody logged in."""
        self._sessions: dict[int, GameSession] = {}
        self._personas: dict[int, GameSession] = {}

    def log_in(self, session: GameSession) -> None:
        """Register a player's login and send the account's earlier session away.

        Args:
            session: A session whose account just logged in.
        """
        assert session.account is not None
        earlier = self._sessions.get(session.account.id)
        if earlier is not None and earlier is not session:
            logger.info(
                "Account %d logged in again, ending its earlier session", session.account.id
            )
            earlier.disconnect(LOGGED_IN_ELSEWHERE)
            earlier.forget_login()
        self._sessions[session.account.id] = session

    def log_out(self, session: GameSession) -> None:
        """Forget a session's login, unless the account has moved to another session.

        Args:
            session: A session that ended.
        """
        if session.account is not None and self._sessions.get(session.account.id) is session:
            del self._sessions[session.account.id]

    def playing(self, persona_id: int) -> GameSession | None:
        """Return the session a player's persona plays in, when it is online.

        Args:
            persona_id: The persona's number.
        """
        return self._personas.get(persona_id)

    def play(self, session: GameSession) -> None:
        """Register the persona that a player's session plays as.

        Args:
            session: A session whose persona just logged in.
        """
        assert session.persona is not None
        self._personas[session.persona.id] = session

    def leave(self, session: GameSession, persona: Persona) -> None:
        """Forget that a session plays as a persona.

        Args:
            session: The session.
            persona: The persona it played as.
        """
        if self._personas.get(persona.id) is session:
            del self._personas[persona.id]

    def find_key(self, key: str) -> GameSession | None:
        """Return the player session whose persona a session key belongs to.

        Args:
            key: The key that NuLoginPersona handed out, as the game's HTTP requests carry it.
        """
        wanted = key.encode()
        return next(
            (
                session
                for session in self._personas.values()
                if session.persona_key and hmac.compare_digest(session.persona_key.encode(), wanted)
            ),
            None,
        )


class GameSession:
    """One game process, as one WebSocket of the hook carries it, its connections and its login."""

    def __init__(
        self,
        routes: Mapping[tuple[str, str], Route[Any]],
        players: Players,
        address: Address | None,
    ) -> None:
        """Start a session for a game process.

        Args:
            routes: The FESL handlers by service command and TXN.
            players: Where player logins are registered.
            address: The public address of the player's PC, when known.
        """
        self.address = address
        self.account: Account | None = None
        self.account_key: str | None = None
        self.persona: Persona | None = None
        self.persona_key: str | None = None
        self._routes = routes
        self._players = players
        self._fesl: FeslConnection | None = None

    def open(self, opened: Open, outbox: Outbox) -> Connection:
        """Start handling a connection that the game opened; Theater is not served yet.

        Args:
            opened: The host the game looked up and the ports on both ends.
            outbox: Where the connection's packets go.
        """
        if opened.connection.channel is Channel.FESL:
            return FeslConnection(self, outbox, self._routes)
        logger.info("Refusing a %s connection to %s", opened.connection.channel.name, opened.host)
        outbox.close()
        return _Refused()

    def log_in(self, account: Account, key: str, connection: FeslConnection) -> None:
        """Make an account the session's login.

        Dedicated servers of a game share their account, so only players are kept to one session.

        Args:
            account: The account that logged in.
            key: The session key handed to the game.
            connection: The FESL connection the login came on.
        """
        self.forget_persona()
        if self.account is not None and self.account.id != account.id:
            self._players.log_out(self)
        self.account = account
        self.account_key = key
        self._fesl = connection
        if not account.server:
            self._players.log_in(self)

    def log_in_persona(self, persona: Persona, key: str) -> None:
        """Make a persona of the session's account the one it plays as.

        Args:
            persona: The persona.
            key: The session key handed to the game for it.
        """
        if self.persona != persona:
            self.forget_persona()
        self.persona = persona
        self.persona_key = key
        # Dedicated servers of a game share their persona, so no push could pick one of them.
        if self.account is not None and not self.account.server:
            self._players.play(self)

    def forget_persona(self) -> None:
        """Stop playing as the session's persona, as when it was disabled."""
        persona = self.persona
        self.persona = None
        self.persona_key = None
        if persona is not None:
            self._players.leave(self, persona)

    def forget_login(self) -> None:
        """Drop the session's login, as when its account moved to another session."""
        self.account = None
        self.account_key = None
        self.forget_persona()

    def disconnect(self, reason: int) -> None:
        """Send the game away on the connection it logged in on.

        Args:
            reason: The Goodbye reason.
        """
        if self._fesl is not None:
            self._fesl.disconnect(reason)

    def push(self, command: str, txn: str, message: Message) -> None:
        """Send the game a transaction on the FESL connection it logged in on.

        Args:
            command: The service, such as asso.
            txn: The transaction's name.
            message: Its fields.
        """
        if self._fesl is not None:
            self._fesl.push(command, txn, message)

    async def end(self) -> None:
        """Forget the session after all of its connections have closed."""
        self.forget_persona()
        self._players.log_out(self)


class _Refused:
    async def receive(self, packet: Packet) -> None:
        pass

    async def close(self) -> None:
        pass
