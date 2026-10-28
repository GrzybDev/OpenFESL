import logging

from openfesl.core.entitlements import Entitlement, EntitlementStore
from openfesl.core.games import Game
from openfesl.core.launcher import LauncherSession, LauncherSessions
from openfesl.core.personas import Persona, PersonaStore
from openfesl.core.session import Players
from openfesl.core.trust import ServerTrust
from openfesl.core.wallets import Wallets

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

    A dedicated server's session also reads the personas whose data the server keeps.
    """

    def __init__(
        self,
        sessions: LauncherSessions,
        players: Players,
        trust: ServerTrust,
        personas: PersonaStore,
        entitlements: EntitlementStore,
        wallets: Wallets,
    ) -> None:
        """Find the askers among the launcher's sign-ins and their personas in the stores.

        Args:
            sessions: The launcher's sign-ins, by session id.
            players: The game sessions that accounts are logged in on.
            trust: Which dedicated servers keep the data of which personas.
            personas: Where the personas are found.
            entitlements: Where the personas' items are kept.
            wallets: The balances the personas spend.
        """
        self._sessions = sessions
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
        persona = await self._persona(session_id, game, persona_id)
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
        persona = await self._persona(session_id, game, persona_id)
        return await self._wallets.of_persona(game, persona)

    async def _persona(self, session_id: str | None, game: Game, persona_id: int) -> Persona:
        account_id = self.session(session_id).account_id
        persona = await self._personas.find_by_id(persona_id)
        if (
            persona is None
            or persona.namespace != game.namespace
            or not (persona.account_id == account_id or await self._keeps(account_id, persona))
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
