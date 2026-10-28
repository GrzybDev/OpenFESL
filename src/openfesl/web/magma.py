from typing import Annotated

from fastapi import Cookie, FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.magma import NOT_NUMERIC, VALIDATION_FAILED, Magma, MagmaError
from openfesl.protocol.magma import (
    EntitlementEntry,
    entitlements,
    error,
    products,
    success,
    token,
)

XML = "text/xml"

# The session id the game was started with, which it sends as its magma cookie.
SessionId = Annotated[str | None, Cookie(alias="magma")]
# The original service names a persona in a path userId, also in its refusals.
UserId = Annotated[int, Path(alias="userId")]


def magma_app(game: Game, magma: Magma) -> FastAPI:
    """Return the application that answers a game's requests to its Magma host.

    The game reads the document and never the HTTP status: a <success> root or the list asked
    for holds the answer, and an <error> root fails the request with its code and cause in the
    game's log.

    Args:
        game: The game whose Magma host it answers.
        magma: Answers about who asks.
    """
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        exception_handlers={MagmaError: _refused, RequestValidationError: _not_numeric},
    )

    @app.get("/nucleus/authToken")
    async def auth_token(session_id: SessionId = None) -> Response:
        return _xml(token(magma.session(session_id).token))

    @app.get("/nucleus/entitlements/{userId}")
    async def items(
        persona_id: UserId,
        session_id: SessionId = None,
        tag: Annotated[str, Query(alias="entitlementTag")] = "",
    ) -> Response:
        held = await magma.entitlements(session_id, game, persona_id, tag)
        entries = [
            EntitlementEntry(
                entitlement.id,
                entitlement.tag,
                entitlement.uses or 0,
                entitlement.granted,
                entitlement.expires,
                entitlement.status,
            )
            for entitlement in held
        ]
        return _xml(entitlements(entries))

    # The store has no products, while a game still needs both lists to load a persona.
    @app.get("/ofb/products")
    @app.get("/{language}/game/hotDeals")
    async def offers() -> Response:
        return _xml(products())

    # Telemetry the game can do without, so it is not kept; any root but <success> is a denial
    # that the game logs.
    @app.post("/dc/submit")
    @app.post("/dc/submitTelemetry")
    async def data_collection() -> Response:
        return _xml(success())

    return app


async def _refused(request: Request, refused: MagmaError) -> Response:
    return _xml(error(refused.code, refused.cause))


async def _not_numeric(request: Request, invalid: RequestValidationError) -> Response:
    # Only the ids in paths are checked, by the names the original service gives them.
    field = invalid.errors()[0]["loc"][-1]
    return _xml(error(VALIDATION_FAILED, NOT_NUMERIC, str(field)))


def _xml(document: bytes) -> Response:
    return Response(document, media_type=XML)
