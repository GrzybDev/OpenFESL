import asyncio
from typing import Any

from openfesl.core.associations import AssociationStore, blocking
from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.personas import NOT_LOGGED_IN, UNKNOWN_PERSONA, Persona, PersonaStore
from openfesl.core.session import GameSession, Players, playing_player
from openfesl.protocol.message import Message
from openfesl.protocol.pres import (
    AsyncPresenceStatusEvent,
    PresenceAnswer,
    PresenceOutcome,
    PresenceRequests,
    PresenceUser,
    SetPresenceStatus,
    Status,
)

MAXIMUM_REQUESTS = 500
OFFLINE = Status(show="disc")


class PresenceService:
    """The pres transactions: players' statuses and the subscriptions that push them to others.

    Statuses and subscriptions live in memory only; a player's status ends when its persona
    stops playing, and so do the subscriptions its session made.
    """

    def __init__(
        self, personas: PersonaStore, players: Players, associations: AssociationStore
    ) -> None:
        """Start without statuses or subscriptions.

        Args:
            personas: Where the personas that players subscribe to are looked up.
            players: Where the personas that play are found, and where they leave.
            associations: Where the lists are kept on which players block others.
        """
        self._personas = personas
        self._associations = associations
        self._statuses: dict[int, Status] = {}
        self._subscribers: dict[int, set[GameSession]] = {}
        self._subscriptions: dict[GameSession, set[int]] = {}
        players.on_leave(self._leave)

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("pres", "PresenceSubscribe"): Route(PresenceRequests, self.subscribe),
            ("pres", "PresenceUnsubscribe"): Route(PresenceRequests, self.unsubscribe),
            ("pres", "SetPresenceStatus"): Route(SetPresenceStatus, self.set_status),
        }

    async def subscribe(
        self, connection: FeslConnection, request: PresenceRequests
    ) -> PresenceAnswer:
        """Subscribe the player to the statuses of personas of its game.

        A persona that blocked the player answers as unknown. The answer is followed by the
        status of each subscribed persona that has one.

        Args:
            connection: The FESL connection the request came on.
            request: The personas.

        Raises:
            TransactionError: When nobody plays, a dedicated server asks or the request names
                too many personas.
        """
        session, player = playing_player(connection)
        if len(request.requests) > MAXIMUM_REQUESTS:
            raise TransactionError(PARAMETERS_ERROR)
        found = [await self._personas.find_by_id(wanted.user_id) for wanted in request.requests]
        types = blocking(connection.game) if connection.game else []
        owners = {persona.id for persona in found if persona is not None}
        blocked = await self._associations.listed_by(player.id, types, owners)
        # The lookups gave the game time to switch personas, which ends the subscriptions.
        if session.persona != player:
            raise TransactionError(NOT_LOGGED_IN)
        responses = []
        snapshot = []
        for wanted, persona in zip(request.requests, found, strict=True):
            if persona is None or persona.namespace != player.namespace or persona.id in blocked:
                user = PresenceUser(id=wanted.user_id)
                responses.append(PresenceOutcome(owner=user, outcome=UNKNOWN_PERSONA))
                continue
            self._subscriptions.setdefault(session, set()).add(persona.id)
            self._subscribers.setdefault(persona.id, set()).add(session)
            responses.append(PresenceOutcome(owner=_user(persona), outcome=0))
            if (status := self._statuses.get(persona.id)) is not None:
                snapshot.append(_event(persona, status, initial=True))
        if snapshot:
            # The game drops a status of anyone it holds no subscription answer for, so the
            # snapshot goes out once the answer has.
            asyncio.get_running_loop().call_soon(_push, session, snapshot)
        return PresenceAnswer(responses=responses)

    async def unsubscribe(
        self, connection: FeslConnection, request: PresenceRequests
    ) -> PresenceAnswer:
        """End the player's subscriptions to personas; ending one it lacks succeeds too.

        Args:
            connection: The FESL connection the request came on.
            request: The personas.

        Raises:
            TransactionError: When nobody plays, or a dedicated server asks.
        """
        session, _ = playing_player(connection)
        for wanted in request.requests:
            self._unsubscribe(session, wanted.user_id)
        return PresenceAnswer(
            responses=[
                PresenceOutcome(owner=PresenceUser(id=wanted.user_id), outcome=0)
                for wanted in request.requests
            ]
        )

    async def set_status(self, connection: FeslConnection, request: SetPresenceStatus) -> Message:
        """Keep the player's status and push it to its subscribers.

        Args:
            connection: The FESL connection the request came on.
            request: The status.

        Raises:
            TransactionError: When nobody plays, or a dedicated server asks.
        """
        _, player = playing_player(connection)
        if request.status.show == OFFLINE.show:
            self._statuses.pop(player.id, None)
        else:
            self._statuses[player.id] = request.status
        self._publish(player, request.status)
        return Message()

    def _leave(self, session: GameSession, persona: Persona) -> None:
        for target in self._subscriptions.pop(session, set()):
            self._unsubscribe(session, target)
        if self._statuses.pop(persona.id, None) is not None:
            self._publish(persona, OFFLINE)

    def _unsubscribe(self, session: GameSession, target: int) -> None:
        self._subscriptions.get(session, set()).discard(target)
        subscribers = self._subscribers.get(target)
        if subscribers is not None:
            subscribers.discard(session)
            if not subscribers:
                del self._subscribers[target]

    def _publish(self, persona: Persona, status: Status) -> None:
        event = _event(persona, status, initial=False)
        for subscriber in self._subscribers.get(persona.id, set()):
            subscriber.push("pres", "AsyncPresenceStatusEvent", event)


def _user(persona: Persona) -> PresenceUser:
    return PresenceUser(id=persona.id, name=persona.name)


def _event(persona: Persona, status: Status, *, initial: bool) -> AsyncPresenceStatusEvent:
    return AsyncPresenceStatusEvent(owner=_user(persona), initial=initial, status=status)


def _push(session: GameSession, events: list[AsyncPresenceStatusEvent]) -> None:
    for event in events:
        session.push("pres", "AsyncPresenceStatusEvent", event)
