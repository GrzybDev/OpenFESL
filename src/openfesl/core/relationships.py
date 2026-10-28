import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from weakref import WeakKeyDictionary

from openfesl.core.games import Game
from openfesl.core.relationship_rules import RelationshipRules
from openfesl.core.session import GameSession, Players

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
    LIST_FULL = "LIST_FULL"
    UNKNOWN_RELATIONSHIP = "RELATIONSHIP_NOT_FOUND"
    STATUS_TOO_LONG = "STATUS_TOO_LONG"
    STATUS_NOT_TEXT = "STATUS_NOT_TEXT"


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
class Relationship:
    """A relationship between two accounts as one of them sees it.

    Attributes:
        id: The number this account names it by; the other account's side has its own.
        tag: The list it is on, such as friends.
        member_id: The other account.
        name: The other account's display name.
        own: Whether this account asked for it or accepted it.
        theirs: Whether the other account asked for it or accepted it.
        new: Whether the other account changed it since this account last acknowledged it.
        modified: When either account last changed it.
    """

    id: int
    tag: str
    member_id: int
    name: str
    own: bool
    theirs: bool
    new: bool
    modified: datetime

    @property
    def mutual(self) -> bool:
        """Whether both accounts agreed to it, as friends have."""
        return self.own and self.theirs


@dataclass(frozen=True, slots=True)
class Presence:
    """What an account's game last reported of what it does, kept while that game is logged in.

    Attributes:
        account_id: The account.
        text: The game's status line, read back only by games.
    """

    account_id: int
    text: str


class RelationshipStore(Protocol):
    """Where the relationships between accounts are kept."""

    async def roster(self, game: str, account_id: int) -> list[Relationship]:
        """Return an account's relationships in a game with accounts that are in use, oldest first.

        Args:
            game: The game's id.
            account_id: The account.
        """

    async def increase(
        self, game: str, tag: str, account_id: int, member_id: int, most: int
    ) -> Relationship:
        """Ask another account for a relationship, or accept the one it asked for.

        Args:
            game: The game's id.
            tag: The list it is on.
            account_id: The account that asks or accepts.
            member_id: The other account.
            most: The most relationships of the tag the account may ask for or accept.

        Returns:
            The relationship as the asking account sees it now.

        Raises:
            RelationshipRefused: When either account is not one that others may ask, as one
                out of use or without a display name, or the asking account has the most
                relationships it may have.
        """

    async def decrease(
        self, game: str, tag: str, account_id: int, member_id: int
    ) -> Relationship | None:
        """End a relationship on both sides, whoever asked for it.

        Args:
            game: The game's id.
            tag: The list it is on.
            account_id: The account that ends it.
            member_id: The other account.

        Returns:
            The relationship as the account sees it now that neither side wants it; none when
            there was none.
        """

    async def acknowledge(self, game: str, account_id: int, relationship_id: int) -> bool:
        """Mark an account's side of a relationship seen, so that it is no longer new to it.

        Args:
            game: The game's id.
            account_id: The account.
            relationship_id: The number the account names the relationship by.

        Returns:
            Whether the account has such a relationship.
        """


class Relationships:
    """The relationships between a game's accounts and the status lines their games report."""

    def __init__(self, store: RelationshipStore, players: Players) -> None:
        """Keep relationships in a store and status lines while their games are logged in.

        Args:
            store: Where the relationships are kept.
            players: The game sessions that accounts are logged in on.
        """
        self._store = store
        self._players = players
        # Weakly, so a status line goes with the session of the game that reported it.
        self._presence: WeakKeyDictionary[GameSession, Presence] = WeakKeyDictionary()

    async def roster(self, game: Game, account_id: int) -> list[Relationship]:
        """Return an account's relationships with accounts that are in use, oldest first.

        Args:
            game: The game.
            account_id: The account.
        """
        return await self._store.roster(game.id, account_id)

    async def increase(self, game: Game, account_id: int, member_id: int, tag: str) -> Relationship:
        """Ask another account for a relationship, or accept the one it asked for.

        Args:
            game: The game.
            account_id: The account that asks or accepts.
            member_id: The other account.
            tag: The list it is to be on.

        Raises:
            RelationshipRefused: When the account asks itself, the game keeps no such list,
                either account may not be asked or the account's list is full.
        """
        rules = _rules(game)
        _check(rules, account_id, member_id, tag)
        relationship = await self._store.increase(game.id, tag, account_id, member_id, rules.most)
        logger.info(
            "Account %d asked for or accepted a relationship with account %d", account_id, member_id
        )
        return relationship

    async def decrease(self, game: Game, account_id: int, member_id: int, tag: str) -> Relationship:
        """End a relationship on both sides: cancel a request, decline one or remove a friend.

        Args:
            game: The game.
            account_id: The account that ends it.
            member_id: The other account.
            tag: The list it is on.

        Returns:
            The relationship as the account sees it now that neither side wants it.

        Raises:
            RelationshipRefused: When the game keeps no such list or there is no such
                relationship.
        """
        _check(_rules(game), account_id, member_id, tag)
        ended = await self._store.decrease(game.id, tag, account_id, member_id)
        if ended is None:
            raise RelationshipRefused(Refusal.UNKNOWN_RELATIONSHIP)
        logger.info("Account %d ended its relationship with account %d", account_id, member_id)
        return ended

    async def acknowledge(self, game: Game, account_id: int, relationship_id: int) -> None:
        """Mark a relationship seen by an account, which its game does once it showed it.

        Args:
            game: The game.
            account_id: The account.
            relationship_id: The number the account names the relationship by.

        Raises:
            RelationshipRefused: When the account has no such relationship.
        """
        if not await self._store.acknowledge(game.id, account_id, relationship_id):
            raise RelationshipRefused(Refusal.UNKNOWN_RELATIONSHIP)

    def report(self, account_id: int, text: str) -> None:
        """Keep the status line that an account's game reports while that game is logged in.

        A report from an account that no game is logged in with is dropped.

        Args:
            account_id: The account.
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
        """Return what the game of a relationship's other account last reported, while it plays.

        Only friends see each other's presence; a request shows none.

        Args:
            relationship: The relationship.
        """
        if not relationship.mutual:
            return None
        account_id = relationship.member_id
        session = self._players.online(account_id)
        presence = self._presence.get(session) if session is not None else None
        # A session that another account logged in on keeps nothing of the earlier one.
        return presence if presence is not None and presence.account_id == account_id else None


def _check(rules: RelationshipRules, account_id: int, member_id: int, tag: str) -> None:
    if tag != rules.friends:
        raise RelationshipRefused(Refusal.UNKNOWN_TAG)
    if member_id == account_id:
        raise RelationshipRefused(Refusal.SELF)


def _rules(game: Game) -> RelationshipRules:
    if game.relationships is None:
        raise ValueError(f"{game.name} keeps no relationships")
    return game.relationships
