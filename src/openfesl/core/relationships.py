import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from weakref import WeakKeyDictionary

from openfesl.core.games import Game
from openfesl.core.hosted_games import HostedGame
from openfesl.core.hosting import GAME, Hosting
from openfesl.core.relationship_rules import RelationshipRules
from openfesl.core.session import GameSession, Players
from openfesl.core.trust import ServerTrust

logger = logging.getLogger(__name__)

# A game's status line names little more than its hero and server, so anything longer is no
# status line.
LONGEST_STATUS = 1024
# The characters XML 1.0 cannot carry, which would make every roster showing the line unreadable.
_NOT_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


class Refusal(StrEnum):
    """Why a relationship was left as it was; each value is the cause Magma reports."""

    SELF = "SELF_RELATIONSHIP"
    UNKNOWN_TAG = "UNKNOWN_TAG"
    UNKNOWN_USER = "USER_NOT_FOUND"
    NAMELESS = "NO_DISPLAY_NAME"
    UNKNOWN_SERVER = "SERVER_NOT_FOUND"
    LIST_FULL = "LIST_FULL"
    UNKNOWN_RELATIONSHIP = "RELATIONSHIP_NOT_FOUND"
    STATUS_TOO_LONG = "STATUS_TOO_LONG"
    STATUS_NOT_TEXT = "STATUS_NOT_TEXT"
    SEARCH_TOO_SHORT = "SEARCH_TOO_SHORT"


class RelationshipRefused(Exception):
    """Refuses a change of a relationship."""

    def __init__(self, refusal: Refusal) -> None:
        """Refuse a change for a reason.

        Args:
            refusal: Why it was refused.
        """
        super().__init__(refusal)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class Party:
    """An account or a dedicated server, as one side of a relationship.

    Attributes:
        account_id: The account; none for a server.
        server: The name a server goes by; none for an account.
    """

    account_id: int | None = None
    server: str | None = None


@dataclass(frozen=True, slots=True)
class Relationship:
    """A relationship as one of its sides sees it.

    Attributes:
        id: The number this side names it by; the other side has its own.
        tag: The list it is on, such as friends.
        member: The other side.
        name: The other side's display name, or a server's name.
        own: Whether this side asked for it or accepted it.
        theirs: Whether the other side asked for it or accepted it.
        new: Whether the other side changed it since this side last acknowledged it.
        modified: When either side last changed it.
    """

    id: int
    tag: str
    member: Party
    name: str
    own: bool
    theirs: bool
    new: bool
    modified: datetime

    @property
    def mutual(self) -> bool:
        """Whether both sides agreed to it, as friends have."""
        return self.own and self.theirs


@dataclass(frozen=True, slots=True)
class Presence:
    """What a game last reported of what it does, kept while it is logged in.

    Attributes:
        account_id: The account the game is logged in with.
        text: The game's status line, read back only by games.
    """

    account_id: int
    text: str


@dataclass(frozen=True, slots=True)
class Found:
    """A player that a search found by one of its names.

    Attributes:
        account_id: The player's account.
        name: The name that matched.
        hero: Whether the name is one of its heroes' rather than its account's display name.
    """

    account_id: int
    name: str
    hero: bool


class RelationshipStore(Protocol):
    """Where the relationships between accounts and dedicated servers are kept."""

    async def roster(self, game: str, owner: Party) -> list[Relationship]:
        """Return the relationships of one side in a game with servers and accounts in use.

        Args:
            game: The game's id.
            owner: The side.

        Returns:
            The relationships, oldest first.
        """

    async def increase(
        self, game: str, tag: str, owner: Party, member: Party, most: int
    ) -> Relationship:
        """Ask another side for a relationship, or accept the one it asked for.

        Args:
            game: The game's id.
            tag: The list it is on.
            owner: The side that asks or accepts.
            member: The other side.
            most: The most relationships of the tag the asking side may ask for or accept.

        Returns:
            The relationship as the asking side sees it now.

        Raises:
            RelationshipRefused: When either side is an account that others may not ask, as
                one out of use or without a display name, or the asking side has the most
                relationships it may have.
        """

    async def decrease(
        self, game: str, tag: str, owner: Party, member: Party, *, ends: bool
    ) -> Relationship | None:
        """Withdraw one side's agreement to a relationship, or end it on both sides.

        A relationship that neither side agrees to is gone.

        Args:
            game: The game's id.
            tag: The list it is on.
            owner: The side that withdraws.
            member: The other side.
            ends: Whether the relationship ends on both sides, whoever asked for it.

        Returns:
            The relationship as the withdrawing side sees it now; none when there was none.
        """

    async def acknowledge(self, game: str, owner: Party, relationship_id: int) -> bool:
        """Mark one side of a relationship seen, so that it is no longer new to that side.

        Args:
            game: The game's id.
            owner: The side.
            relationship_id: The number the side names the relationship by.

        Returns:
            Whether the side has such a relationship.
        """

    async def search(self, namespace: str, pattern: str, limit: int) -> list[Found]:
        """Return the accounts that may be asked, by display name, and their heroes by name.

        Args:
            namespace: The namespace of the game's heroes.
            pattern: The start of the names, where * stands for any run of characters; capitals
                do not matter.
            limit: The most names to return, by name.
        """


class Relationships:
    """The relationships of a game's accounts and servers and the status lines of their games."""

    def __init__(
        self, store: RelationshipStore, players: Players, hosting: Hosting, trust: ServerTrust
    ) -> None:
        """Keep relationships in a store and status lines while their games are logged in.

        Args:
            store: Where the relationships are kept.
            players: The game sessions that accounts are logged in on.
            hosting: The games that dedicated servers host, which name the servers.
            trust: Which dedicated servers host games that players see.
        """
        self._store = store
        self._players = players
        self._hosting = hosting
        self._trust = trust
        # Weakly, so a status line goes with the session of the game that reported it.
        self._presence: WeakKeyDictionary[GameSession, Presence] = WeakKeyDictionary()

    async def roster(self, game: Game, owner: Party) -> list[Relationship]:
        """Return the relationships of an account or a server with those in use, oldest first.

        Args:
            game: The game.
            owner: The account or server.
        """
        return await self._store.roster(game.id, owner)

    async def increase(self, game: Game, owner: Party, member: Party, tag: str) -> Relationship:
        """Ask another side for a relationship, or accept the one it asked for.

        Accounts are friends with accounts and on the VIP lists of servers; an account asks only
        servers that host a game players see.

        Args:
            game: The game.
            owner: The account or server that asks or accepts.
            member: The other side.
            tag: The list it is to be on.

        Raises:
            RelationshipRefused: When a side asks itself, the game keeps no such list, either
                side may not be asked or the asking side's list is full.
        """
        rules = _rules(game)
        _check(rules, owner, member, tag)
        if member.server is not None:
            session = self._server_named(member.server)
            if session is None or session.game is not game or not await self._trust.serves(session):
                raise RelationshipRefused(Refusal.UNKNOWN_SERVER)
        relationship = await self._store.increase(game.id, tag, owner, member, rules.most)
        logger.info("%s asked for or accepted a relationship with %s", owner, member)
        return relationship

    async def decrease(self, game: Game, owner: Party, member: Party, tag: str) -> Relationship:
        """Withdraw from a relationship: cancel or decline a request, remove a friend or a VIP.

        An account ends the relationship on both sides. A server only withdraws its own
        agreement, so that a player it takes off its list sees its VIP status revoked.

        Args:
            game: The game.
            owner: The account or server that withdraws.
            member: The other side.
            tag: The list it is on.

        Returns:
            The relationship as the withdrawing side sees it now.

        Raises:
            RelationshipRefused: When the game keeps no such list or there is no such
                relationship.
        """
        _check(_rules(game), owner, member, tag)
        ends = owner.account_id is not None
        changed = await self._store.decrease(game.id, tag, owner, member, ends=ends)
        if changed is None:
            raise RelationshipRefused(Refusal.UNKNOWN_RELATIONSHIP)
        logger.info("%s withdrew from its relationship with %s", owner, member)
        return changed

    async def acknowledge(self, game: Game, owner: Party, relationship_id: int) -> None:
        """Mark a relationship seen by one side, which its game does once it showed it.

        Args:
            game: The game.
            owner: The account or server.
            relationship_id: The number the side names the relationship by.

        Raises:
            RelationshipRefused: When the side has no such relationship.
        """
        if not await self._store.acknowledge(game.id, owner, relationship_id):
            raise RelationshipRefused(Refusal.UNKNOWN_RELATIONSHIP)

    async def search(self, game: Game, pattern: str) -> list[Found]:
        """Return the players whose display names or heroes' names start as a search says.

        Only players that may be asked for a relationship are found.

        Args:
            game: The game, whose heroes are searched.
            pattern: The start of the names, where * stands for any run of characters; capitals
                do not matter.

        Raises:
            RelationshipRefused: When the search names too few characters besides wildcards.
        """
        rules = _rules(game)
        if len(pattern.replace("*", "")) < rules.search_minimum:
            raise RelationshipRefused(Refusal.SEARCH_TOO_SHORT)
        return await self._store.search(game.namespace, pattern, rules.search_limit)

    def report(self, account_id: int, text: str) -> None:
        """Keep the status line that a game reports while it is logged in with an account.

        A report from an account that no game is logged in with is dropped.

        Args:
            account_id: The account; a dedicated server's is the one it logged in with.
            text: The status line.

        Raises:
            RelationshipRefused: When the line is too long to be one or holds characters that
                XML cannot carry.
        """
        if len(text) > LONGEST_STATUS:
            raise RelationshipRefused(Refusal.STATUS_TOO_LONG)
        if _NOT_TEXT.search(text):
            raise RelationshipRefused(Refusal.STATUS_NOT_TEXT)
        session = self._players.online(account_id)
        if session is not None:
            self._presence[session] = Presence(account_id, text)

    def presence_of(self, relationship: Relationship) -> Presence | None:
        """Return what the game of a relationship's other side last reported, while it runs.

        Only sides that both agreed see each other's presence; a request shows none.

        Args:
            relationship: The relationship.
        """
        member = relationship.member
        session: GameSession | None = None
        if relationship.mutual and member.server is not None:
            session = self._server_named(member.server)
        elif relationship.mutual and member.account_id is not None:
            session = self._players.online(member.account_id)
        presence = self._presence.get(session) if session is not None else None
        # A session that another account logged in on keeps nothing of the earlier one.
        logged_in = session.account if session is not None else None
        if presence is None or logged_in is None or presence.account_id != logged_in.id:
            return None
        return presence

    def names(self, session: GameSession) -> set[str]:
        """Return the names a dedicated server goes by: its game's persistent id and its hero's.

        Args:
            session: The dedicated server's session.
        """
        hosted = self._trust.served_by(session)
        return _names(hosted) if hosted is not None else set()

    def _server_named(self, server: str) -> GameSession | None:
        return next(
            (
                hosted.host.session
                for hosted in self._hosting.every()
                if hosted.type == GAME and hosted.server and server in _names(hosted)
            ),
            None,
        )


def _check(rules: RelationshipRules, owner: Party, member: Party, tag: str) -> None:
    # Accounts are friends with each other, and servers keep accounts on their VIP lists.
    with_server = owner.server is not None or member.server is not None
    servers_only = owner.account_id is None and member.account_id is None
    if servers_only or tag != (rules.vip if with_server else rules.friends):
        raise RelationshipRefused(Refusal.UNKNOWN_TAG)
    if member == owner:
        raise RelationshipRefused(Refusal.SELF)


def _names(hosted: HostedGame) -> set[str]:
    # The game calls what a server goes by its GUID, which is a hosted game's persistent id;
    # whether a 1.89 server sends that or its hero's name is not known yet, so either is taken.
    persona = hosted.host.session.persona
    return {hosted.ugid, persona.name} if persona is not None else {hosted.ugid}


def _rules(game: Game) -> RelationshipRules:
    if game.relationships is None:
        raise ValueError(f"{game.name} keeps no relationships")
    return game.relationships
