from collections.abc import Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openfesl.core.game_http import GameHttp

GAME_HOST = "X-OpenFESL-Host"
# Neither a proxy nor the game's own HTTP stack may keep or rewrite what the game fetches.
CACHE_CONTROL = "no-store, no-transform"


class GameHttpMiddleware:
    """Answers the game's HTTP, which the hook forwards with the game's host in a header of its own.

    Requests to a game's Magma host go to that game's Magma application, the others to the
    editorial files and lockers. Requests without that header, such as a browser's, reach the
    application's own routes.
    """

    def __init__(self, app: ASGIApp, http: GameHttp, magma: Mapping[str, ASGIApp]) -> None:
        """Put the game's HTTP in front of an application.

        Args:
            app: The application that serves every other request.
            http: Answers the game's requests to hosts other than its Magma host.
            magma: Answers the games' requests to their Magma hosts, by host.
        """
        self._app = app
        self._http = http
        self._magma = {host.lower(): answers for host, answers in magma.items()}

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

        async def send_uncached(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = CACHE_CONTROL
            await send(message)

        magma = self._magma.get(host.split(":")[0].lower())
        if magma is not None:
            await magma(scope, receive, send_uncached)
            return
        request = Request(scope)
        answer = self._http.answer(request.method, host, request.url.path, request.query_params)
        response = Response(answer.body, answer.status, media_type=answer.media_type)
        await response(scope, receive, send_uncached)
