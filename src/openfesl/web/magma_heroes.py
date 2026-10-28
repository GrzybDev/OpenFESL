from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import PlainTextResponse, Response
from starlette.datastructures import UploadFile

from openfesl.core.games import Game
from openfesl.core.magma import USER_DOES_NOT_MATCH, VALIDATION_FAILED, Magma, MagmaError
from openfesl.core.mugshots import LARGEST_FILE, NOT_A_MUGSHOT, Mugshots
from openfesl.protocol.magma import success
from openfesl.web.magma import XML, SessionId

# The original service names an account or a persona in a path userId, also in its refusals.
UserId = Annotated[int, Path(alias="userId")]
PersonaId = Annotated[int, Path(alias="personaId")]
# A mugshot's file and the form around it.
LARGEST_UPLOAD = LARGEST_FILE + 1024
# The most of a refused body that is read before the answer.
LARGEST_DISCARD = 16 * LARGEST_UPLOAD


def hero_router(game: Game, magma: Magma, mugshots: Mugshots) -> APIRouter:
    """Return the routes about the asking game's own account and heroes.

    Args:
        game: The game whose Magma host they answer.
        magma: Answers about who asks.
        mugshots: Keeps the mugshots that the game renders of its heroes.
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

    # The game reads the raw answer, 1 to render the hero and upload it, anything else not to.
    @router.get("/api/mugshot/mayupload/{personaId}", response_class=PlainTextResponse)
    async def may_upload(persona_id: PersonaId, session_id: SessionId = None) -> str:
        try:
            await magma.persona(session_id, game, persona_id, own=True)
        except MagmaError:
            return "0"
        return "1"

    # What the path's last segment says is not known; a hero has one mugshot, whatever it is.
    @router.post("/api/mugshot/upload/{personaId}/{segment}")
    async def upload(
        persona_id: PersonaId, segment: str, request: Request, session_id: SessionId = None
    ) -> Response:
        persona = await magma.persona(session_id, game, persona_id, own=True)
        # The game sends its length, so that a body too long for a mugshot goes unparsed.
        if int(request.headers.get("content-length") or LARGEST_UPLOAD + 1) > LARGEST_UPLOAD:
            await _discard(request)
            raise MagmaError(VALIDATION_FAILED, NOT_A_MUGSHOT)
        form = await request.form(max_files=1, max_fields=0)
        picture = form.get("file")
        if not isinstance(picture, UploadFile):
            raise MagmaError(VALIDATION_FAILED, NOT_A_MUGSHOT)
        await mugshots.replace(persona, await picture.read(LARGEST_FILE + 1))
        return _xml(success())

    return router


async def _discard(request: Request) -> None:
    # A connection closed on a body still arriving is reset, losing the answer sent before.
    discarded = 0
    async for chunk in request.stream():
        discarded += len(chunk)
        if discarded > LARGEST_DISCARD:
            break


def _xml(document: bytes) -> Response:
    return Response(document, media_type=XML)
