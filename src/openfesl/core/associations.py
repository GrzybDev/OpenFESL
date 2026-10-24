import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Any, Protocol

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game, ListRule
from openfesl.core.personas import NOT_LOGGED_IN, Persona, PersonaStore
from openfesl.core.session import Players
from openfesl.protocol.asso import (
    AddAssociations,
    AssociationOutcome,
    AssociationRequest,
    AssociationsAnswer,
    DeleteAssociations,
    Entity,
    GetAssociationCountAnswer,
    GetAssociations,
    GetAssociationsAnswer,
    MemberInfo,
    NotifyAssociationUpdate,
    Owner,
    Partition,
)

logger = logging.getLogger(__name__)

# The codes the SDK turns into its own owner, member and list-full errors.
OWNER_INVALID = 23001
MEMBER_INVALID = 23002
LIST_FULL = 23005

PERSONA = 1
ROLL = "RollLeastRecentlyModified"
MAXIMUM_REQUESTS = 100


@dataclass(frozen=True, slots=True)
class OwnedList:
    """One association list: a persona's members of one type within a domain partition.

    Attributes:
        owner_id: The persona the list belongs to.
        type: The list's type, such as PlasmaFriends.
        partition: The domain partition as `/domain/subDomain[/key]`, empty for none.
    """

    owner_id: int
    type: str
    partition: str


@dataclass(frozen=True, slots=True)
class Member:
    """A persona on a list.

    Attributes:
        id: The persona's number.
        name: The persona's name.
        created: When it was added.
        modified: When it was last added again, which decides who rolls off a full list.
    """

    id: int
    name: str
    created: datetime
    modified: datetime


@dataclass(frozen=True, slots=True)
class Change:
    """A member added to or removed from a list.

    Attributes:
        owned: The list.
        member: The member.
        size: How many members the list holds afterwards.
        removed: Whether the member left the list rather than joining it.
    """

    owned: OwnedList
    member: Member
    size: int
    removed: bool = False


class AssociationStore(Protocol):
    """Where association lists are kept."""

    async def members(self, owned: OwnedList) -> list[Member]:
        """Return a list's members whose personas are in use, oldest first.

        Args:
            owned: The list.
        """

    async def count(self, owned: OwnedList) -> int:
        """Return how many members a list holds whose personas are in use.

        Args:
            owned: The list.
        """

    async def add(
        self, owned: OwnedList, member_id: int, limit: int, *, roll: bool, mutual: bool
    ) -> list[Change] | None:
        """Add a member to a list, or mark it modified when it is a member already.

        Args:
            owned: The list.
            member_id: The persona to add.
            limit: The most members a list holds.
            roll: Whether the owner's full list drops its least recently modified member to
                make room.
            mutual: Whether the member's own list of the type gains the owner too, once the
                member agreed by listing the owner itself.

        Returns:
            The owner's change, then the member's when its list changed, then the member that
            the owner's full list dropped; None when a list is full and nothing changed.
        """

    async def delete(self, owned: OwnedList, member_id: int, *, mutual: bool) -> list[Change]:
        """Remove a member from a list.

        Args:
            owned: The list.
            member_id: The persona to remove.
            mutual: Whether the member's own list of the type loses the owner too.

        Returns:
            A change for each list that held the member.
        """


class AssociationService:
    """The asso transactions that read and change association lists, such as friends."""

    def __init__(self, store: AssociationStore, personas: PersonaStore, players: Players) -> None:
        """Serve association lists from a store.

        Args:
            store: Where the lists are kept.
            personas: Where owners and members are looked up.
            players: Where the owners of changed lists are found online.
        """
        self._store = store
        self._personas = personas
        self._players = players

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("asso", "GetAssociations"): Route(GetAssociations, self.get),
            ("asso", "GetAssociationCount"): Route(GetAssociations, self.count),
            ("asso", "AddAssociations"): Route(AddAssociations, self.add),
            ("asso", "DeleteAssociations"): Route(DeleteAssociations, self.delete),
        }

    async def get(
        self, connection: FeslConnection, request: GetAssociations
    ) -> GetAssociationsAnswer:
        """Return a list's members.

        A list the game does not keep, of another type or partition, reads as an empty list
        that takes nobody, since the game's login fails when a list it loads at login fails.

        Args:
            connection: The FESL connection the request came on.
            request: The list.

        Raises:
            TransactionError: When nobody plays, or the owner is not the session's own persona
                or, for a list that dedicated servers keep, a server's player.
        """
        rule, owned, owner = await self._read(connection, request)
        members = await self._store.members(owned) if rule else []
        return GetAssociationsAnswer(
            type=request.type,
            domain_partition=request.domain_partition,
            owner=_entity(owner),
            max_list_size=rule.size if rule else 0,
            members=[_member(member) for member in members],
        )

    async def count(
        self, connection: FeslConnection, request: GetAssociations
    ) -> GetAssociationCountAnswer:
        """Return how many members a list holds.

        Args:
            connection: The FESL connection the request came on.
            request: The list.

        Raises:
            TransactionError: As for GetAssociations.
        """
        rule, owned, _ = await self._read(connection, request)
        return GetAssociationCountAnswer(
            max_list_size=rule.size if rule else 0,
            count=await self._store.count(owned) if rule else 0,
        )

    async def add(self, connection: FeslConnection, request: AddAssociations) -> AssociationsAnswer:
        """Add members to lists, pushing the change to each other owner whose list changed.

        The member of a mutual list gains the owner on its own list only once it agreed, so
        that nobody puts itself on another player's friends list.

        Args:
            connection: The FESL connection the request came on.
            request: The list type and the owner and member of each addition.

        Raises:
            TransactionError: When nobody plays, the game keeps no such list or the request
                holds too many additions.
        """
        actor, rule, reach = self._change(
            connection, request.type, request.domain_partition, request.add_requests
        )
        outcomes = []
        for item in request.add_requests:
            try:
                owner, member = await self._pair(actor, reach, item)
            except _Refused as refused:
                outcomes.append(_refusal(item, refused.code, rule))
                continue
            owned = OwnedList(owner.id, request.type, _partition(request.domain_partition))
            changes = await self._store.add(
                owned,
                member.id,
                rule.size,
                # A mutual list never drops a member, who would keep the owner on its own.
                roll=request.list_full_behavior == ROLL and not rule.mutual,
                mutual=rule.mutual,
            )
            if changes is None:
                outcomes.append(_refusal(item, LIST_FULL, rule))
                continue
            outcomes.append(_outcome(owner, _member(changes[0].member), changes[0].size, rule))
            # A member added again only counts as modified; the other owners hear of new ones,
            # and an owner hears of the member its full list dropped, which no answer names.
            dropped = [change for change in changes if change.removed]
            new = [
                change
                for change in changes
                if not change.removed and change.member.created == change.member.modified
            ]
            self._notify(actor.id, request.domain_partition, new, rule)
            self._notify(None, request.domain_partition, dropped, rule)
        return AssociationsAnswer(
            type=request.type, domain_partition=request.domain_partition, result=outcomes
        )

    async def delete(
        self, connection: FeslConnection, request: DeleteAssociations
    ) -> AssociationsAnswer:
        """Remove members from lists, pushing the change to each other owner whose list changed.

        Removing a persona that is not a member succeeds, since the list then is as asked.

        Args:
            connection: The FESL connection the request came on.
            request: The list type and the owner and member of each removal.

        Raises:
            TransactionError: As for AddAssociations.
        """
        actor, rule, reach = self._change(
            connection, request.type, request.domain_partition, request.delete_requests
        )
        outcomes = []
        for item in request.delete_requests:
            try:
                owner, member = await self._pair(actor, reach, item)
            except _Refused as refused:
                outcomes.append(_refusal(item, refused.code, rule))
                continue
            owned = OwnedList(owner.id, request.type, _partition(request.domain_partition))
            changes = await self._store.delete(owned, member.id, mutual=rule.mutual)
            own = next((change for change in changes if change.owned == owned), None)
            if own is None:
                removed = MemberInfo(id=member.id, type=PERSONA, name=member.name)
                outcomes.append(_outcome(owner, removed, await self._store.count(owned), rule))
            else:
                outcomes.append(_outcome(owner, _member(own.member), own.size, rule))
            self._notify(actor.id, request.domain_partition, changes, rule)
        return AssociationsAnswer(
            type=request.type, domain_partition=request.domain_partition, result=outcomes
        )

    async def _read(
        self, connection: FeslConnection, request: GetAssociations
    ) -> tuple[ListRule | None, OwnedList, Persona]:
        actor, game = _context(connection)
        rule = _rule(game, request.type, request.domain_partition)
        if rule is None:
            partition = _partition(request.domain_partition)
            logger.warning(
                "%s keeps no association list %r in %r", game.name, request.type, partition
            )
        reach = self._reach(connection, rule, change=False)
        owner = await self._owner(actor, request.owner, reach)
        if owner is None:
            raise TransactionError(OWNER_INVALID)
        return rule, OwnedList(owner.id, request.type, _partition(request.domain_partition)), owner

    def _change(
        self,
        connection: FeslConnection,
        list_type: str,
        partition: Partition,
        items: list[AssociationRequest],
    ) -> tuple[Persona, ListRule, _Reach]:
        actor, game = _context(connection)
        rule = _rule(game, list_type, partition)
        if rule is None or len(items) > MAXIMUM_REQUESTS:
            raise TransactionError(PARAMETERS_ERROR)
        return actor, rule, self._reach(connection, rule, change=True)

    def _reach(self, connection: FeslConnection, rule: ListRule | None, *, change: bool) -> _Reach:
        # Players use their own lists but only read the ones the game's dedicated servers keep
        # for them; a server keeps those for every player and has only its own of the others.
        kept = rule is not None and rule.kept_by_servers
        account = connection.session.account
        if account is not None and account.server:
            return _Reach.ANYONE if kept else _Reach.OWN
        return _Reach.NOBODY if kept and change else _Reach.OWN

    async def _owner(self, actor: Persona, wanted: Owner, reach: _Reach) -> Persona | None:
        if wanted.type != PERSONA or reach is _Reach.NOBODY:
            return None
        if wanted.id == actor.id:
            return actor
        return await self._persona(actor, wanted.id) if reach is _Reach.ANYONE else None

    async def _persona(self, actor: Persona, persona_id: int) -> Persona | None:
        persona = await self._personas.find_by_id(persona_id)
        return persona if persona is not None and persona.namespace == actor.namespace else None

    async def _pair(
        self, actor: Persona, reach: _Reach, item: AssociationRequest
    ) -> tuple[Persona, Persona]:
        owner = await self._owner(actor, item.owner, reach)
        if owner is None:
            raise _Refused(OWNER_INVALID)
        member = None
        if item.member.type == PERSONA and item.member.id != owner.id:
            member = await self._persona(actor, item.member.id)
        if member is None:
            raise _Refused(MEMBER_INVALID)
        return owner, member

    def _notify(
        self, answered: int | None, partition: Partition, changes: list[Change], rule: ListRule
    ) -> None:
        # The answer tells the requester of the changes to its own lists.
        for change in changes:
            session = self._players.playing(change.owned.owner_id)
            if change.owned.owner_id == answered or session is None or session.persona is None:
                continue
            session.push(
                "asso",
                "NotifyAssociationUpdate",
                NotifyAssociationUpdate(
                    type=change.owned.type,
                    domain_partition=partition,
                    owner=_entity(session.persona),
                    member=_member(change.member),
                    list_size=change.size,
                    mutual=rule.mutual,
                    operation="del" if change.removed else "add",
                ),
            )


class _Reach(Enum):
    """Whose lists of a type a session may use."""

    NOBODY = auto()
    OWN = auto()
    ANYONE = auto()


class _Refused(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _context(connection: FeslConnection) -> tuple[Persona, Game]:
    persona = connection.session.persona
    if persona is None or connection.game is None:
        raise TransactionError(NOT_LOGGED_IN)
    return persona, connection.game


def _rule(game: Game, list_type: str, partition: Partition) -> ListRule | None:
    # The game keeps its lists in no partition or in its own; any other partition would give a
    # player as many lists of a type as it names.
    own = Partition(domain=game.partition.domain, sub_domain=game.partition.sub_domain)
    return game.associations.get(list_type) if partition in (Partition(), own) else None


def _partition(partition: Partition) -> str:
    return "/".join(("", partition.domain, partition.sub_domain, partition.key)).rstrip("/")


def _entity(persona: Persona) -> Entity:
    return Entity(id=persona.id, type=PERSONA, name=persona.name)


def _member(member: Member) -> MemberInfo:
    return MemberInfo(
        id=member.id,
        type=PERSONA,
        name=member.name,
        created=member.created,
        modified=member.modified,
    )


def _outcome(owner: Persona, member: MemberInfo, size: int, rule: ListRule) -> AssociationOutcome:
    return AssociationOutcome(
        owner=_entity(owner), member=member, list_size=size, outcome=0, mutual=rule.mutual
    )


def _refusal(item: AssociationRequest, code: int, rule: ListRule) -> AssociationOutcome:
    return AssociationOutcome(
        owner=Entity(id=item.owner.id, type=item.owner.type),
        member=MemberInfo(id=item.member.id, type=item.member.type),
        outcome=code,
        mutual=rule.mutual,
    )
