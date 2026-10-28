from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from openfesl.core.launcher import LauncherError, LauncherService, Refusal

PATH = "/launcher/session"


class SignIn(BaseModel):
    """What a launcher sends to sign a player in before it starts a game or a server.

    It signs in with an account's name and password, or with the code of a start link that a
    page of the site made.

    Attributes:
        game: The game's data-file name.
        role: Whether the game is started to play or to host.
        account: The account's login name.
        password: The account's password.
        code: The start link's code.
    """

    game: str
    role: Literal["play", "host"] = "play"
    account: str | None = None
    password: str | None = None
    code: str | None = None

    @model_validator(mode="after")
    def _one_way_in(self) -> SignIn:
        if (self.code is None) == (self.account is None or self.password is None):
            raise ValueError("either a code or an account and its password")
        return self


def launcher_routes(service: Callable[[], LauncherService]) -> APIRouter:
    """Return the route a launcher signs in on before it starts the game.

    A refusal names its reason as a code, which the launcher words in the player's language.

    Args:
        service: Returns the launcher service, once the application has started.
    """
    router = APIRouter()

    @router.post(PATH)
    async def sign_in(request: SignIn) -> JSONResponse:
        launcher = service()
        try:
            if request.code is not None:
                session_id, session = await launcher.redeem(
                    request.game, request.role, request.code
                )
            else:
                assert request.account is not None and request.password is not None
                session_id, session = await launcher.sign_in(
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
