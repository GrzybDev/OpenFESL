from typing import Annotated

from fastapi import Cookie, FastAPI
from fastapi.responses import Response

from openfesl.core.magma import Magma, MagmaError
from openfesl.protocol.magma import error, success, token

XML = "text/xml"


def magma_app(magma: Magma) -> FastAPI:
    """Return the application that answers the games' requests to their Magma hosts.

    The game reads the document and never the HTTP status: a <success> root holds the answer,
    and an <error> root fails the request with its code and cause in the game's log.

    Args:
        magma: Finds who asks.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/nucleus/authToken")
    async def auth_token(
        session_id: Annotated[str | None, Cookie(alias="magma")] = None,
    ) -> Response:
        try:
            session = magma.session(session_id)
        except MagmaError as refused:
            return _xml(error(refused.code, refused.cause))
        return _xml(token(session.token))

    # Telemetry the game can do without, so it is not kept; any root but <success> is a denial
    # that the game logs.
    @app.post("/dc/submit")
    @app.post("/dc/submitTelemetry")
    async def data_collection() -> Response:
        return _xml(success())

    return app


def _xml(document: bytes) -> Response:
    return Response(document, media_type=XML)
