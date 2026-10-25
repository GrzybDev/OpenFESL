import hmac
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from openfesl.core.accounts import Account, AccountState
from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.connection import Address, Connection, Outbox
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game
from openfesl.core.personas import NOT_LOGGED_IN, Persona
from openfesl.core.theater import Theater, TheaterConnection
from openfesl.protocol.message import Message
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import Channel, Open

logger = logging.getLogger(__name__)

LOGGED_IN_ELSEWHERE = 2
AUTHORIZATION_FAILED = 4
TOO_MANY_LOGINS = 121


class Players:
    """The sessions that players are logged in on, so that an account plays in one place.

    The personas they play as are known too, so that what other players do reaches them.
    """

    def __init__(self) -> None:
        """Start with nobody logged in."""
        self._sessions: dict[int, GameSession] = {}
        self._personas: dict[int, GameSession] = {}
        self._leaving: list[Callable[[GameSession, Persona], object]] = []

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

    def follow(self, feed: AdminFeed) -> None:
        """Sign players out whose accounts the admin takes out of use while they are online.

        Args:
            feed: The admin's actions as they are recorded.
        """
        feed.on(AdminAction.ACCOUNT_STATE, self._apply_state)

    def _apply_state(self, action: LoggedAction) -> None:
        session = self._sessions.get(int(action.target))
        state = AccountState(action.details["state"])
        if session is not None and state is not AccountState.ACTIVE:
            logger.info("Signing account %s out: the admin set it %s", action.target, state)
            session.sign_out(banned=state is AccountState.BANNED)

    def online(self, account_id: int) -> GameSession | None:
        """Return the session a player's account is logged in on, when it is online.

        Args:
            account_id: The account's number.
        """
        return self._sessions.get(account_id)

    def logged_in(self) -> int:
        """Return how many accounts are logged in now, in any game."""
        return len(self._sessions)

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

    def playing_in(self, game: Game) -> list[Persona]:
        """Return the personas that players play a game as now, first logged in first.

        Args:
            game: The game.
        """
        return [
            session.persona
            for session in self._personas.values()
            if session.persona is not None and session.game is game
        ]

    def play(self, session: GameSession) -> None:
        """Register the persona that a player's session plays as.

        Args:
            session: A session whose persona just logged in.
        """
        assert session.persona is not None
        self._personas[session.persona.id] = session

    def leave(self, session: GameSession, persona: Persona) -> None:
        """Forget that a session plays as a persona, and tell whoever listens.

        A session that the persona has moved away from tells nobody, since the persona still
        plays elsewhere.

        Args:
            session: The session.
            persona: The persona it played as.
        """
        if self._personas.get(persona.id) is not session:
            return
        del self._personas[persona.id]
        for listener in self._leaving:
            listener(session, persona)

    def on_leave(self, listener: Callable[[GameSession, Persona], object]) -> None:
        """Call a function whenever a persona stops playing in a session.

        Args:
            listener: Called with the session and the persona that left it.
        """
        self._leaving.append(listener)

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
        theater: Theater,
        hook_version: str | None,
    ) -> None:
        """Start a session for a game process.

        Args:
            routes: The FESL handlers by service command and TXN.
            players: Where player logins are registered.
            address: The public address of the player's PC, when known.
            theater: The Theater service its Theater connections and datagrams go to.
            hook_version: The version of OpenFESLHook that carries the game, when it names one.
        """
        self.address = address
        self.hook_version = hook_version
        self.logged_in_at: datetime | None = None
        self.account: Account | None = None
        self.account_key: str | None = None
        self.persona: Persona | None = None
        self.persona_key: str | None = None
        self._routes = routes
        self._players = players
        self._theater = theater
        self._fesl: FeslConnection | None = None
        self._theater_connections: set[TheaterConnection] = set()

    @property
    def game(self) -> Game | None:
        """The game that the session's login came from."""
        return self._fesl.game if self._fesl is not None else None

    @property
    def client_string(self) -> str | None:
        """The client string that the game's Hello named on the connection it logged in on."""
        hello = self._fesl.hello if self._fesl is not None else None
        return hello.client_string if hello is not None else None

    def open(self, opened: Open, outbox: Outbox) -> Connection:
        """Start handling a connection that the game opened, or close one to an unknown host.

        Args:
            opened: The host the game looked up and the ports on both ends.
            outbox: Where the connection's packets go.
        """
        if opened.connection.channel is Channel.FESL:
            return FeslConnection(self, outbox, self._routes)
        if connection := self._theater.open(self, opened, outbox):
            if isinstance(connection, TheaterConnection):
                self._theater_connections.add(connection)
                connection.on_close(lambda: self._theater_connections.discard(connection))
            return connection
        logger.info("Refusing a %s connection to %s", opened.connection.channel.name, opened.host)
        outbox.close()
        return _Refused()

    def admit(self, account: Account, *, replace: bool) -> None:
        """Check that an account may log in on the session.

        Dedicated servers of a game share their account, so only players are kept to one session.

        Args:
            account: The account that logs in.
            replace: Whether the login of a player that is logged in elsewhere signs the other
                session out; otherwise it is refused.

        Raises:
            TransactionError: When the player is logged in elsewhere and may not replace it.
        """
        earlier = self._players.online(account.id)
        if not replace and not account.server and earlier is not None and earlier is not self:
            raise TransactionError(TOO_MANY_LOGINS)

    def log_in(
        self, account: Account, key: str, connection: FeslConnection, *, replace: bool
    ) -> None:
        """Make an account the session's login.

        Args:
            account: The account that logged in.
            key: The session key handed to the game.
            connection: The FESL connection the login came on.
            replace: Whether the login of a player that is logged in elsewhere signs the other
                session out; otherwise it is refused.

        Raises:
            TransactionError: When the player is logged in elsewhere and may not replace it.
        """
        self.admit(account, replace=replace)
        self.forget_persona()
        if self.account is not None and self.account.id != account.id:
            self._players.log_out(self)
        self.account = account
        self.account_key = key
        self.logged_in_at = datetime.now(UTC)
        self._fesl = connection
        if not account.server:
            self._players.log_in(self)

    def log_in_persona(self, persona: Persona, key: str) -> None:
        """Make a persona of the session's account the one it plays as.

        Args:
            persona: The persona.
            key: The session key handed to the game for it.

        Raises:
            TransactionError: When the account is no longer the session's, as when it logged
                in elsewhere while the persona was looked up.
        """
        if self.account is None or self.account.id != persona.account_id:
            raise TransactionError(NOT_LOGGED_IN)
        if self.persona != persona:
            self.forget_persona()
        self.persona = persona
        self.persona_key = key
        # Dedicated servers of a game share their persona, so no push could pick one of them.
        if not self.account.server:
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

    def sign_out(self, *, banned: bool) -> None:
        """Send the game away, its connections closed, as when the admin disabled its account.

        Args:
            banned: Whether the account was banned, which only Theater's DISC makes the game
                name; otherwise the game reads that its authorization failed.
        """
        theaters = list(self._theater_connections)
        if banned:
            for theater in theaters:
                theater.send("DISC", None)
        self.disconnect(AUTHORIZATION_FAILED)
        for theater in theaters:
            theater.close_connection()
        self._players.log_out(self)
        self.forget_login()

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

    @property
    def busy(self) -> bool:
        """Whether the game falls behind with what it is sent, so that what can wait should."""
        return self._fesl is not None and self._fesl.busy

    async def end(self) -> None:
        """Forget the session after all of its connections have closed."""
        self.forget_persona()
        self._players.log_out(self)


def playing_player(connection: FeslConnection) -> tuple[GameSession, Persona]:
    """Return the session and persona of the player that plays on a connection.

    Args:
        connection: The FESL connection a request came on.

    Raises:
        TransactionError: When nobody plays, or a dedicated server asks, since the servers of a
            game share one persona that is nobody's.
    """
    session = connection.session
    if session.account is None or session.persona is None:
        raise TransactionError(NOT_LOGGED_IN)
    if session.account.server:
        raise TransactionError(PARAMETERS_ERROR)
    return session, session.persona


class _Refused:
    async def receive(self, packet: Packet) -> None:
        pass

    async def close(self) -> None:
        pass
