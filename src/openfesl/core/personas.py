import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from openfesl.core.accounts import ALREADY_REGISTERED, Account
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game
from openfesl.protocol.acct import (
    FoundOwner,
    NuGetPersonas,
    NuGetPersonasAnswer,
    NuLoginPersonaAnswer,
    NuLookupUserInfo,
    NuLookupUserInfoAnswer,
    NuSearchOwners,
    NuSearchOwnersAnswer,
    NuSuggestPersonas,
    NuSuggestPersonasAnswer,
    UserInfo,
)
from openfesl.protocol.acct import (
    Persona as PersonaRequest,
)
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)

NOT_LOGGED_IN = 20
UNKNOWN_PERSONA = 101
TOO_MANY_PERSONAS = 163
# The game shows its "disabled by customer support" text for it.
DISABLED_BY_ADMIN = 164

# The owner type that names a persona in the SDK's owner fields.
PERSONA = 1

MAXIMUM_PERSONAS = 8
MAXIMUM_LOOKUPS = 64
MAXIMUM_SUGGESTIONS = 10
SERVER_PERSONA_LENGTH = 14

# Dedicated servers name their persona after the part of their login before '@', dots included.
_NAME = re.compile(r"[A-Za-z0-9_.-]{3,16}")


class PersonaState(StrEnum):
    """Whether the admin lets a persona play, apart from its player removing it."""

    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class Persona:
    """A name an account plays under within a namespace.

    Attributes:
        id: The persona's number, which the game knows as its user id.
        account_id: The account it belongs to.
        namespace: The namespace its name is unique in.
        name: The name.
    """

    id: int
    account_id: int
    namespace: str
    name: str


@dataclass(frozen=True, slots=True)
class PersonaRecord:
    """A persona with what the admin panel shows about it.

    Attributes:
        persona: The persona.
        created: When its player created it.
        removed: When its player removed it, if they did.
        state: Whether the admin lets it play.
    """

    persona: Persona
    created: datetime
    removed: datetime | None
    state: PersonaState


class PersonaStore(Protocol):
    """Where personas are kept."""

    async def of_account(self, account_id: int, namespace: str) -> list[Persona]:
        """Return an account's enabled personas in a namespace, oldest first.

        Args:
            account_id: The account.
            namespace: The namespace.
        """

    async def find(self, namespace: str, name: str) -> Persona | None:
        """Return the enabled persona with a name, matched regardless of case.

        Args:
            namespace: The namespace.
            name: The name.
        """

    async def find_by_id(self, persona_id: int) -> Persona | None:
        """Return an enabled persona by its number.

        Args:
            persona_id: The persona's number.
        """

    async def search(self, namespace: str, pattern: str, limit: int) -> list[Persona]:
        """Return players' enabled personas whose names match a pattern regardless of case.

        Args:
            namespace: The namespace.
            pattern: The name, where * stands for any run of characters.
            limit: The most personas to return, by name.
        """

    async def add(self, account_id: int, namespace: str, name: str) -> Persona | None:
        """Create a persona, or return None when the name is taken in the namespace.

        Args:
            account_id: The account it belongs to.
            namespace: The namespace.
            name: The name.
        """

    async def disable(self, persona_id: int) -> None:
        """Take a persona out of use; its name stays taken.

        Args:
            persona_id: The persona's number.
        """

    async def named(self, name: str) -> list[Persona]:
        """Return players' personas with a name in every namespace, those they disabled too.

        Args:
            name: The name, matched regardless of case.
        """

    async def blocked(self, namespace: str, name: str) -> bool:
        """Return whether the admin disabled the persona with a name.

        Args:
            namespace: The namespace.
            name: The name, matched regardless of case.
        """

    async def set_state(self, persona_id: int, state: PersonaState, admin: str | None) -> None:
        """Change whether a persona may play and record the change among the admin's actions.

        Args:
            persona_id: The persona's number.
            state: Its new state.
            admin: The admin who changed it; None for the command line.
        """


class PersonaService:
    """The acct transactions that list, create, disable and log in personas."""

    def __init__(self, store: PersonaStore) -> None:
        """Serve personas from a store.

        Args:
            store: Where personas are kept.
        """
        self._store = store

    async def prepare(self, servers: list[tuple[Game, Account]]) -> None:
        """Create the persona each game's dedicated servers play as, before a player can take it.

        A dedicated server logs in with the first persona listed, and without one creates one
        named after its login up to the '@', cut to 14 characters, as this persona is named.

        Args:
            servers: Each game with its dedicated servers' account.
        """
        for game, account in servers:
            name = account.nuid.split("@")[0][:SERVER_PERSONA_LENGTH]
            await self._store.add(account.id, game.namespace, name)
            persona = await self._store.find(game.namespace, name)
            if persona is None or persona.account_id != account.id:
                logger.error("The dedicated servers' persona %s belongs to a player", name)

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("acct", "NuGetPersonas"): Route(NuGetPersonas, self.get_personas),
            ("acct", "NuAddPersona"): Route(PersonaRequest, self.add),
            ("acct", "NuDisablePersona"): Route(PersonaRequest, self.disable),
            ("acct", "NuLoginPersona"): Route(PersonaRequest, self.login),
            ("acct", "NuLookupUserInfo"): Route(NuLookupUserInfo, self.lookup),
            ("acct", "NuSuggestPersonas"): Route(NuSuggestPersonas, self.suggest),
            ("acct", "NuSearchOwners"): Route(NuSearchOwners, self.search),
        }

    async def get_personas(
        self, connection: FeslConnection, request: NuGetPersonas
    ) -> NuGetPersonasAnswer:
        """Return the account's personas in the game's namespace.

        Args:
            connection: The FESL connection the request came on.
            request: The request; this SDK leaves its namespace empty for the game's own.
        """
        account, namespace = _context(connection)
        personas = await self._store.of_account(account.id, request.namespace or namespace)
        return NuGetPersonasAnswer(personas=[persona.name for persona in personas])

    async def add(self, connection: FeslConnection, request: PersonaRequest) -> Message:
        """Create a persona for the account.

        Args:
            connection: The FESL connection the request came on.
            request: The new persona's name.

        Raises:
            TransactionError: When the name is invalid, taken or disabled by the admin, or the
                account has its maximum.
        """
        account, namespace = _context(connection)
        # The dedicated servers' persona exists already; their shared login proves nothing.
        if account.server:
            raise TransactionError(PARAMETERS_ERROR)
        if not _NAME.fullmatch(request.name):
            raise TransactionError(PARAMETERS_ERROR)
        if len(await self._store.of_account(account.id, namespace)) >= MAXIMUM_PERSONAS:
            raise TransactionError(TOO_MANY_PERSONAS)
        persona = await self._store.add(account.id, namespace, request.name)
        if persona is None:
            blocked = await self._store.blocked(namespace, request.name)
            raise TransactionError(DISABLED_BY_ADMIN if blocked else ALREADY_REGISTERED)
        logger.info("Account %d created persona %d (%s)", account.id, persona.id, persona.name)
        return Message()

    async def disable(self, connection: FeslConnection, request: PersonaRequest) -> Message:
        """Take one of the account's personas out of use.

        Args:
            connection: The FESL connection the request came on.
            request: The persona's name.

        Raises:
            TransactionError: When the account has no persona of that name.
        """
        account, _ = _context(connection)
        if account.server:
            raise TransactionError(PARAMETERS_ERROR)
        persona = await self._own(connection, request.name)
        await self._store.disable(persona.id)
        if connection.session.persona and connection.session.persona.id == persona.id:
            connection.session.forget_persona()
        return Message()

    async def suggest(
        self, connection: FeslConnection, request: NuSuggestPersonas
    ) -> NuSuggestPersonasAnswer:
        """Suggest free names close to one that was taken.

        Args:
            connection: The FESL connection the request came on.
            request: The wanted name and how many suggestions the game shows.
        """
        _, namespace = _context(connection)
        wanted = min(request.max_suggestions, MAXIMUM_SUGGESTIONS)
        base = re.sub(r"[^A-Za-z0-9_.-]", "", request.name)[:12] or "Player"
        names: list[str] = []
        for number in range(1, 100):
            if len(names) >= wanted:
                break
            name = f"{base}{number}"
            if _NAME.fullmatch(name) and await self._store.find(namespace, name) is None:
                names.append(name)
        return NuSuggestPersonasAnswer(names=names)

    async def login(
        self, connection: FeslConnection, request: PersonaRequest
    ) -> NuLoginPersonaAnswer:
        """Make one of the account's personas the session's player.

        Args:
            connection: The FESL connection the request came on.
            request: The persona's name.

        Raises:
            TransactionError: When the account has no persona of that name or the admin
                disabled it.
        """
        persona = await self._own(connection, request.name)
        if await self._store.blocked(persona.namespace, persona.name):
            raise TransactionError(DISABLED_BY_ADMIN)
        key = secrets.token_urlsafe(20) + "."
        connection.session.log_in_persona(persona, key)
        logger.info(
            "Account %d plays as persona %d (%s)", persona.account_id, persona.id, persona.name
        )
        return NuLoginPersonaAnswer(lkey=key, user_id=persona.id)

    async def lookup(
        self, connection: FeslConnection, request: NuLookupUserInfo
    ) -> NuLookupUserInfoAnswer:
        """Return the personas named or numbered in a lookup, leaving out those not found.

        Args:
            connection: The FESL connection the request came on.
            request: The personas to look up.
        """
        _, namespace = _context(connection)
        found = []
        for wanted in request.user_info[:MAXIMUM_LOOKUPS]:
            if wanted.user_name is not None:
                persona = await self._store.find(namespace, wanted.user_name)
            elif wanted.user_id is not None:
                persona = await self._store.find_by_id(wanted.user_id)
            else:
                persona = None
            if persona is not None:
                found.append(
                    UserInfo(
                        user_name=persona.name,
                        user_id=persona.id,
                        master_user_id=persona.account_id,
                        namespace=persona.namespace,
                    )
                )
        return NuLookupUserInfoAnswer(user_info=found)

    async def search(
        self, connection: FeslConnection, request: NuSearchOwners
    ) -> NuSearchOwnersAnswer:
        """Return the players' personas of the game whose names match a search.

        Dedicated servers' personas are left out, and at most 64 come back, or fewer when the
        game asks for a smaller chunk.

        Args:
            connection: The FESL connection the request came on.
            request: The name, where * stands for any run of characters.
        """
        _, namespace = _context(connection)
        limit = MAXIMUM_LOOKUPS
        if request.chunk_size > 0:
            limit = min(request.chunk_size, limit)
        found = await self._store.search(namespace, request.screen_name, limit)
        return NuSearchOwnersAnswer(
            users=[FoundOwner(name=persona.name, type=PERSONA, id=persona.id) for persona in found]
        )

    async def _own(self, connection: FeslConnection, name: str) -> Persona:
        account, namespace = _context(connection)
        persona = await self._store.find(namespace, name)
        if persona is None or persona.account_id != account.id:
            raise TransactionError(UNKNOWN_PERSONA)
        return persona


def _context(connection: FeslConnection) -> tuple[Account, str]:
    account = connection.session.account
    if account is None or connection.game is None:
        raise TransactionError(NOT_LOGGED_IN)
    return account, connection.game.namespace
