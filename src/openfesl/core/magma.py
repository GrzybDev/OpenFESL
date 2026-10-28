import logging

from openfesl.core.entitlements import Entitlement, EntitlementStore
from openfesl.core.games import Game
from openfesl.core.launcher import LauncherSession, LauncherSessions
from openfesl.core.personas import Persona, PersonaStore
from openfesl.core.relay_tokens import RelayTokens
from openfesl.core.session import GameSession, Players
from openfesl.core.trust import ServerTrust
from openfesl.core.wallets import MOST_BALANCE, Wallets

logger = logging.getLogger(__name__)

# The session id of a game started without one, which it still sends as its magma cookie.
NO_SESSION_ID = "0"

# The code and causes of a request that names no session. The game only logs them, as "code -
# cause"; the original service's own for this case are not known.
NOT_LOGGED_IN = "NOT_LOGGED_IN"
NO_SESSION = "NO_SESSION"
UNKNOWN_SESSION = "UNKNOWN_SESSION"

# The original service's refusals of a persona that a path names, which a game tells apart by
# their causes: one that the asker may not read, or an id that is no number, with its field.
VALIDATION_FAILED = "VALIDATION_FAILED"
USER_DOES_NOT_MATCH = "USER_DOES_NOT_MATCH"
NOT_NUMERIC = "NOT_NUMERIC"
# OpenFESL's own causes for a game process that asks what only a dedicated server may, and for
# a credit of a currency, amount or id that no server may send.
NOT_A_SERVER = "NOT_A_SERVER"
INVALID_CREDIT = "INVALID_CREDIT"
# The original service's refusal of a credit whose id it credited already, which a game tells
# apart from other refusals by its cause.
INTERNAL_ERROR = "INTERNAL_ERROR"
DUPLICATE_TRANSACTION_UUID = "DUPLICATE_TRANSACTION_UUID"
# The id of a credit is a GUID that the game formats, far shorter than this.
MOST_TRANSACTION = 64


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
    """Answers Magma about the personas of the account whose launcher session a cookie names.

    A dedicated server's session also reads the personas whose data the server keeps, and its
    calls without a session are known by the relay token of its game process.
    """

    def __init__(
        self,
        sessions: LauncherSessions,
        relay_tokens: RelayTokens,
        players: Players,
        trust: ServerTrust,
        personas: PersonaStore,
        entitlements: EntitlementStore,
        wallets: Wallets,
    ) -> None:
        """Find the askers among the launcher's sign-ins and their personas in the stores.

        Args:
            sessions: The launcher's sign-ins, by session id.
            relay_tokens: The game processes' relay sessions, by the token of their WebSocket.
            players: The game sessions that accounts are logged in on.
            trust: Which dedicated servers keep the data of which personas.
            personas: Where the personas are found.
            entitlements: Where the personas' items are kept.
            wallets: The balances the personas spend.
        """
        self._sessions = sessions
        self._relay_tokens = relay_tokens
        self._players = players
        self._trust = trust
        self._personas = personas
        self._entitlements = entitlements
        self._wallets = wallets

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

    async def server(self, relay_token: str | None, game: Game) -> GameSession:
        """Return the dedicated server whose game process a request's relay token names.

        Args:
            relay_token: The token the hook added to the request; none when it added none.
            game: The game whose Magma host was asked.

        Raises:
            MagmaError: When the request carries no token or one of no open relay session, or
                the session is no dedicated server of the game hosting a game players see.
        """
        if not relay_token:
            raise MagmaError(NOT_LOGGED_IN, NO_SESSION)
        session = self._relay_tokens.find(relay_token)
        if session is None:
            logger.info("A Magma request carried the relay token of no open session")
            raise MagmaError(NOT_LOGGED_IN, UNKNOWN_SESSION)
        if session.game is not game or not await self._trust.serves(session):
            logger.info("A game process that serves no game asked Magma as a dedicated server")
            raise MagmaError(VALIDATION_FAILED, NOT_A_SERVER)
        return session

    async def name(self, relay_token: str | None, game: Game, account_id: int) -> str:
        """Return the name an account plays as in the game a dedicated server hosts.

        The name is the persona's, never the account's login name, which may be an address.

        Args:
            relay_token: The token the hook added to the request; none when it added none.
            game: The game whose Magma host was asked.
            account_id: The player's account.

        Raises:
            MagmaError: When the token names no dedicated server of the game, or the server
                keeps no persona of the account.
        """
        server = await self.server(relay_token, game)
        persona = await self._trust.persona_of(server, account_id)
        if persona is None:
            logger.info("A dedicated server asked Magma for the name of account %d", account_id)
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        return persona.name

    async def credit(
        self,
        relay_token: str | None,
        game: Game,
        persona_id: int,
        code: str,
        amount: int,
        transaction: str,
    ) -> None:
        """Credit what a dedicated server says that a player's persona earned in its game.

        Args:
            relay_token: The token the hook added to the request; none when it added none.
            game: The game whose Magma host was asked.
            persona_id: The persona.
            code: The currency.
            amount: The amount.
            transaction: The id the server gave the credit, which is credited once.

        Raises:
            MagmaError: When the token names no dedicated server of the game, the server keeps
                no such persona, the game's servers may not credit the currency, the amount or
                id does not fit, or the credit with that id was credited already.
        """
        server = await self.server(relay_token, game)
        persona = await self._personas.find_by_id(persona_id)
        if (
            persona is None
            or persona.namespace != game.namespace
            or not await self._trust.keeps_persona(server, persona.id)
        ):
            logger.info(
                "A dedicated server credited persona %d, which it keeps no data of", persona_id
            )
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        currency = game.currencies.get(code)
        if (
            currency is None
            or currency.credits is None
            or not 1 <= amount <= MOST_BALANCE
            or len(transaction) > MOST_TRANSACTION
        ):
            raise MagmaError(VALIDATION_FAILED, INVALID_CREDIT)
        if await self._wallets.credit_from_server(game, persona, code, amount, transaction) is None:
            raise MagmaError(INTERNAL_ERROR, DUPLICATE_TRANSACTION_UUID)

    async def entitlements(
        self, session_id: str | None, game: Game, persona_id: int, tag: str = ""
    ) -> list[Entitlement]:
        """Return the items that a persona of the asking account owns, one entitlement per item.

        Args:
            session_id: The magma cookie's value; none when the request carries no cookie.
            game: The game whose Magma host was asked.
            persona_id: The persona.
            tag: A tag whose item alone is returned, however the tag says it is owned; every
                item when empty.

        Raises:
            MagmaError: When the request names no session that is known, or the persona is
                not the game's, or neither its account's nor one its account keeps as a server.
        """
        persona = await self.persona(session_id, game, persona_id)
        return await self._entitlements.of_persona(persona.id, tag)

    async def wallets(self, session_id: str | None, game: Game, persona_id: int) -> dict[str, int]:
        """Return what a persona of the asking account has to spend in each currency, by code.

        Args:
            session_id: The magma cookie's value; none when the request carries no cookie.
            game: The game whose Magma host was asked.
            persona_id: The persona.

        Raises:
            MagmaError: When the request names no session that is known, or the persona is
                not the game's, or neither its account's nor one its account keeps as a server.
        """
        persona = await self.persona(session_id, game, persona_id)
        return await self._wallets.of_persona(game, persona)

    async def persona(
        self, session_id: str | None, game: Game, persona_id: int, *, own: bool = False
    ) -> Persona:
        """Return a persona of the game that the asking account owns, or keeps as a server.

        Args:
            session_id: The magma cookie's value; none when the request carries no cookie.
            game: The game whose Magma host was asked.
            persona_id: The persona.
            own: Whether only the account's own personas are returned, for what only a
                player may do, such as buying; a server's players are refused then.

        Raises:
            MagmaError: When the request names no session that is known, or the persona is
                not the game's, or neither the account's nor, unless it must be its own, one
                the account keeps as a server.
        """
        account_id = self.session(session_id).account_id
        persona = await self._personas.find_by_id(persona_id)
        if (
            persona is None
            or persona.namespace != game.namespace
            or not (
                persona.account_id == account_id
                or (not own and await self._keeps(account_id, persona))
            )
        ):
            logger.info(
                "Account %d asked Magma about persona %d, which it may not read",
                account_id,
                persona_id,
            )
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        return persona

    async def _keeps(self, account_id: int, persona: Persona) -> bool:
        # A dedicated server asks with the session of the account it logged in with, about the
        # personas of its players.
        hosting = self._players.online(account_id)
        return hosting is not None and await self._trust.keeps_persona(hosting, persona.id)

    async def serving(self, session_id: str | None, game: Game) -> GameSession:
        """Return the session of the dedicated server whose launcher session a cookie names.

        Args:
            session_id: The magma cookie's value; none when the request carries no cookie.
            game: The game whose Magma host was asked.

        Raises:
            MagmaError: When the request names no session that is known, or its account is not
                logged in as a dedicated server of the game that hosts a game players see.
        """
        account_id = self.session(session_id).account_id
        hosting = self._players.online(account_id)
        if hosting is None or hosting.game is not game or not await self._trust.serves(hosting):
            logger.info("Account %d asked Magma as a server, which it does not run", account_id)
            raise MagmaError(VALIDATION_FAILED, NOT_A_SERVER)
        return hosting
