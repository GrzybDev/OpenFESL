from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.magma import USER_DOES_NOT_MATCH, VALIDATION_FAILED, Magma, MagmaError
from openfesl.protocol.magma import success
from openfesl.web.magma import XML, SessionId

# The original service names an account or a persona in a path userId, also in its refusals.
UserId = Annotated[int, Path(alias="userId")]
PersonaId = Annotated[int, Path(alias="personaId")]


def hero_router(game: Game, magma: Magma) -> APIRouter:
    """Return the routes about the asking game's own account and heroes.

    Args:
        game: The game whose Magma host they answer.
        magma: Answers about who asks.
    """
    router = APIRouter()

    # The game checks its account once logged in and each hero it loads; the check began as
    # one of access to the beta, which every account of this server passes.
    @router.get("/nucleus/check/user/{userId}")
    async def check_user(user_id: UserId, session_id: SessionId = None) -> Response:
        if magma.session(session_id).account_id != user_id:
            raise MagmaError(VALIDATION_FAILED, USER_DOES_NOT_MATCH)
        return _xml(success())

    @router.get("/nucleus/check/persona/{personaId}")
    async def check_persona(persona_id: PersonaId, session_id: SessionId = None) -> Response:
        await magma.persona(session_id, game, persona_id)
        return _xml(success())

    # A dedicated server asks the original service to refresh the profiles it cached of the
    # players whose stats it changed; this server caches none, so the request only succeeds.
    @router.post("/user/updateUserProfile/{userId}")
    async def refresh_profile(user_id: UserId, session_id: SessionId = None) -> Response:
        await magma.user(session_id, game, user_id)
        return _xml(success())

    return router


def _xml(document: bytes) -> Response:
    return Response(document, media_type=XML)
