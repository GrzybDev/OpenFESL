import re
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
    Party,
    Refusal,
    Relationship,
    RelationshipRefused,
    Relationships,
)
from openfesl.core.session import GameSession
from openfesl.protocol.magma import (
    PersonaEntry,
    RelationshipEntry,
    personas,
    relationship,
    roster,
    status_update,
    success,
)
from openfesl.web.magma import RelayToken, SessionId, xml

# What a relationship's paths and entries call an account and a dedicated server.
NUCLEUS = "nucleus"
SERVER = "server"
# The causes of a path that names no account or server, and of a status update without a line.
INVALID_ENTITY = "INVALID_ENTITY"
NO_STATUS = "NO_STATUS"
# A status update's longest body: the longest line at up to four bytes a character, and a
# kilobyte for the tags and a dedicated server's name.
LONGEST_UPDATE = 4 * LONGEST_STATUS + 1024

_ENTITY = re.compile(rf"(?:{NUCLEUS}:(\d+)|{SERVER}:(.+))")
# When a side whose game runs nowhere was last seen: long enough ago for the game to read it as
# gone.
_GONE = datetime(2001, 1, 1, tzinfo=UTC)

RelationshipId = Annotated[int, Path(alias="relationshipId")]


def social_router(game: Game, magma: Magma, relationships: Relationships) -> APIRouter:
    """Return the routes of the relationships of a game's accounts, none for a game without.

    Paths name each side as nucleus:<account> or server:<name>.

    Args:
        game: The game whose Magma host they answer.
        magma: Answers about who asks.
        relationships: Keeps the relationships and the status lines of the games.
    """
    router = APIRouter()
    rules = game.relationships
    if rules is None:
        return router

    async def asking(
        session_id: str | None, relay_token: str | None, owner: str
    ) -> tuple[Party, int, GameSession | None]:
        # Only the game of the side that a path names first may ask: an account's own game, or
        # a dedicated server that goes by the name.
        party = _party(owner)
        if party.server is None:
            account_id = magma.session(session_id).account_id
            if account_id != party.account_id:
                raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
            return party, account_id, None
        serving = await magma.serving(session_id, game, relay_token)
        if party.server not in relationships.names(serving) or serving.account is None:
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        return party, serving.account.id, serving

    async def changing(session_id: str | None, relay_token: str | None, owner: str) -> Party:
        party, _, _ = await asking(session_id, relay_token, owner)
        # The servers' shared session is nobody's, so it asks nobody to be a friend.
        if party.server is None and magma.session(session_id).server:
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        return party

    def entry(found: Relationship) -> RelationshipEntry:
        presence = relationships.presence_of(found)
        member = found.member
        return RelationshipEntry(
            found.id,
            found.new,
            _state(found),
            found.tag,
            member.server if member.server is not None else str(member.account_id),
            SERVER if member.server is not None else NUCLEUS,
            presence.text if presence else "",
            found.name,
            found.modified,
            datetime.now(UTC) if presence else _GONE,
        )

    @router.get("/relationships/roster/{owner}")
    async def whole_roster(
        owner: str, session_id: SessionId = None, relay_token: RelayToken = None
    ) -> Response:
        party, _, _ = await asking(session_id, relay_token, owner)
        listed = await relationships.roster(game, party)
        return xml(roster(entry(found) for found in listed))

    # A dedicated server asks for its VIP list with the states it lists, such as 1,3.
    @router.get("/relationships/roster/{owner}/{tag}/{states}")
    async def tagged_roster(
        owner: str,
        tag: str,
        states: str,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        party, _, _ = await asking(session_id, relay_token, owner)
        wanted = {int(state) for state in states.split(",") if state.isdigit()}
        listed = await relationships.roster(game, party)
        return xml(
            roster(entry(found) for found in listed if found.tag == tag and _state(found) in wanted)
        )

    @router.post("/relationships/increase/{owner}/{member}/{tag}")
    async def increase(
        owner: str,
        member: str,
        tag: str,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        party = await changing(session_id, relay_token, owner)
        with _refusals():
            changed = await relationships.increase(game, party, _party(member), tag)
        return xml(relationship(entry(changed)))

    @router.post("/relationships/decrease/{owner}/{member}/{tag}")
    async def decrease(
        owner: str,
        member: str,
        tag: str,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        party = await changing(session_id, relay_token, owner)
        with _refusals():
            changed = await relationships.decrease(game, party, _party(member), tag)
        return xml(relationship(entry(changed)))

    @router.post("/relationships/acknowledge/{owner}/{relationshipId}")
    async def acknowledge(
        owner: str,
        relationship_id: RelationshipId,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        party = await changing(session_id, relay_token, owner)
        with _refusals():
            await relationships.acknowledge(game, party, relationship_id)
        return xml(success())

    @router.post("/relationships/status/{owner}")
    async def status(
        owner: str,
        request: Request,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        _, account_id, serving = await asking(session_id, relay_token, owner)
        body = await request.body()
        if len(body) > LONGEST_UPDATE:
            raise MagmaError(VALIDATION_FAILED, Refusal.STATUS_TOO_LONG.value)
        text = status_update(body)
        if text is None:
            raise MagmaError(VALIDATION_FAILED, NO_STATUS)
        with _refusals():
            relationships.report(account_id, text, serving)
        return xml(success())

    # A name the player types may hold anything, a slash too.
    @router.get("/nucleus/personas/{search:path}")
    async def search(search: str, session_id: SessionId = None) -> Response:
        magma.session(session_id)
        with _refusals():
            found = await relationships.search(game, search)
        return xml(
            personas(
                PersonaEntry(
                    each.account_id,
                    each.name,
                    game.namespace if each.hero else rules.web_namespace,
                )
                for each in found
            )
        )

    return router


def _party(entity: str) -> Party:
    named = _ENTITY.fullmatch(entity)
    if named is None:
        raise MagmaError(VALIDATION_FAILED, INVALID_ENTITY)
    account, server = named.groups()
    return Party(account_id=int(account)) if account else Party(server=server)


def _state(found: Relationship) -> int:
    # The game reads who agreed as bits: 1 the side whose roster it is, 2 the other side.
    return int(found.own) | int(found.theirs) << 1


@contextmanager
def _refusals() -> Iterator[None]:
    try:
        yield
    except RelationshipRefused as refused:
        raise MagmaError(VALIDATION_FAILED, refused.refusal.value) from None
