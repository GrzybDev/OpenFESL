from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openfesl.core.launcher import LauncherError, LauncherService, Refusal

PATH = "/launcher/session"


class SignIn(BaseModel):
    """What a launcher sends to sign a player in before it starts a game or a server.

    Attributes:
        game: The game's data-file name.
        role: Whether the game is started to play or to host.
        account: The account's login name.
        password: The account's password.
    """

    game: str
    role: Literal["play", "host"] = "play"
    account: str
    password: str


def launcher_routes(service: Callable[[], LauncherService]) -> APIRouter:
    """Return the route a launcher signs in on before it starts the game.

    A refusal names its reason as a code, which the launcher words in the player's language.

    Args:
        service: Returns the launcher service, once the application has started.
    """
    router = APIRouter()

    @router.post(PATH)
    async def sign_in(request: SignIn) -> JSONResponse:
        try:
            session_id, session = await service().sign_in(
                request.game, request.role, request.account, request.password
            )
        except LauncherError as refused:
            status = 404 if refused.refusal is Refusal.UNKNOWN_GAME else 401
            return JSONResponse({"error": refused.refusal.value}, status_code=status)
        return JSONResponse(
            {
                "sessionId": session_id,
                "accountName": session.account_name,
                "personaName": session.persona_name,
            }
        )

    return router
