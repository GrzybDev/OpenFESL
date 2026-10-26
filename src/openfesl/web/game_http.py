from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.game_http import GameHttp

GAME_HOST = "X-OpenFESL-Host"
# Neither a proxy nor the game's own HTTP stack may keep or rewrite what the game fetches.
CACHE_CONTROL = "no-store, no-transform"


class GameHttpMiddleware:
    """Answers the game's HTTP, which the hook forwards with the game's host in a header of its own.

    Requests without that header, such as a browser's, reach the application's own routes.
    """

    def __init__(self, app: ASGIApp, http: GameHttp) -> None:
        """Put the game's HTTP in front of an application.

        Args:
            app: The application that serves every other request.
            http: Answers the game's requests.
        """
        self._app = app
        self._http = http

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer a request of the game, or pass anything else on to the application.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        host = Headers(scope=scope).get(GAME_HOST) if scope["type"] == "http" else None
        if host is None:
            await self._app(scope, receive, send)
            return
        request = Request(scope)
        answer = self._http.answer(
            request.method, host, request.url.path, request.query_params, request.cookies
        )
        response = Response(
            answer.body, answer.status, {"Cache-Control": CACHE_CONTROL}, answer.media_type
        )
        await response(scope, receive, send)
