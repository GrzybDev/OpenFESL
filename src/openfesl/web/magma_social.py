from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.magma import USER_DOES_NOT_MATCH, VALIDATION_FAILED, Magma, MagmaError
from openfesl.core.relationships import (
    LONGEST_STATUS,
    Refusal,
    Relationship,
    RelationshipRefused,
    Relationships,
)
from openfesl.protocol.magma import (
    RelationshipEntry,
    relationship,
    roster,
    status_update,
    success,
)
from openfesl.web.magma import XML, SessionId

# What a relationship's paths and entries call an account.
NUCLEUS = "nucleus"
# The cause of a status update that holds no status line.
NO_STATUS = "NO_STATUS"
# A status update's longest body: the longest line at up to four bytes a character, and a
# kilobyte for the tags.
LONGEST_UPDATE = 4 * LONGEST_STATUS + 1024

# The account a relationship path names first, which is the asking game's own.
AccountId = Annotated[int, Path(alias="userId")]
MemberId = Annotated[int, Path(alias="memberId")]
# When a side whose game runs nowhere was last seen: long enough ago for the game to read it as
# gone.
_GONE = datetime(2001, 1, 1, tzinfo=UTC)
RelationshipId = Annotated[int, Path(alias="relationshipId")]


def social_router(game: Game, magma: Magma, relationships: Relationships) -> APIRouter:
    """Return the routes of the relationships between a game's accounts, none for a game without.

    Args:
        game: The game whose Magma host they answer.
        magma: Answers about who asks.
        relationships: Keeps the relationships and the status lines of the games.
    """
    router = APIRouter()
    if game.relationships is None:
        return router

    def own(session_id: str | None, account_id: int) -> int:
        # The game names its own account first; only a game of that account may.
        if magma.session(session_id).account_id != account_id:
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        return account_id

    def entry(found: Relationship) -> RelationshipEntry:
        presence = relationships.presence_of(found)
        return RelationshipEntry(
            found.id,
            found.new,
            # The game reads who agreed as bits: 1 the account itself, 2 the other side.
            int(found.own) | int(found.theirs) << 1,
            found.tag,
            str(found.member_id),
            NUCLEUS,
            presence.text if presence else "",
            found.name,
            found.modified,
            datetime.now(UTC) if presence else _GONE,
        )

    @router.get("/relationships/roster/nucleus:{userId}")
    async def account_roster(account_id: AccountId, session_id: SessionId = None) -> Response:
        listed = await relationships.roster(game, own(session_id, account_id))
        return _xml(roster(entry(found) for found in listed))

    @router.post("/relationships/increase/nucleus:{userId}/nucleus:{memberId}/{tag}")
    async def increase(
        account_id: AccountId, member_id: MemberId, tag: str, session_id: SessionId = None
    ) -> Response:
        asking = own(session_id, account_id)
        with _refusals():
            changed = await relationships.increase(game, asking, member_id, tag)
        return _xml(relationship(entry(changed)))

    @router.post("/relationships/decrease/nucleus:{userId}/nucleus:{memberId}/{tag}")
    async def decrease(
        account_id: AccountId, member_id: MemberId, tag: str, session_id: SessionId = None
    ) -> Response:
        ending = own(session_id, account_id)
        with _refusals():
            changed = await relationships.decrease(game, ending, member_id, tag)
        return _xml(relationship(entry(changed)))

    @router.post("/relationships/acknowledge/nucleus:{userId}/{relationshipId}")
    async def acknowledge(
        account_id: AccountId, relationship_id: RelationshipId, session_id: SessionId = None
    ) -> Response:
        seeing = own(session_id, account_id)
        with _refusals():
            await relationships.acknowledge(game, seeing, relationship_id)
        return _xml(success())

    @router.post("/relationships/status/nucleus:{userId}")
    async def status(
        account_id: AccountId, request: Request, session_id: SessionId = None
    ) -> Response:
        reporting = own(session_id, account_id)
        body = await request.body()
        if len(body) > LONGEST_UPDATE:
            raise MagmaError(VALIDATION_FAILED, Refusal.STATUS_TOO_LONG.value)
        text = status_update(body)
        if text is None:
            raise MagmaError(VALIDATION_FAILED, NO_STATUS)
        with _refusals():
            relationships.report(reporting, text)
        return _xml(success())

    return router


@contextmanager
def _refusals() -> Iterator[None]:
    try:
        yield
    except RelationshipRefused as refused:
        raise MagmaError(VALIDATION_FAILED, refused.refusal.value) from None


def _xml(document: bytes) -> Response:
    return Response(document, media_type=XML)
