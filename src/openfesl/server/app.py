from fastapi import FastAPI, WebSocket
from fastapi.responses import PlainTextResponse

from openfesl.core.session import GameSession
from openfesl.relay.endpoint import relay_endpoint
from openfesl.server.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Join the layers into the ASGI application that Uvicorn serves.

    Args:
        settings: The deployment's settings; read from the environment when omitted.
    """
    settings = settings or Settings()

    def start_session(websocket: WebSocket) -> GameSession:
        return GameSession(settings.client_address(websocket))

    app = FastAPI(title="OpenFESL", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_api_websocket_route("/", relay_endpoint(start_session))

    @app.get("/health", response_class=PlainTextResponse)
    async def health() -> str:
        return "ok"

    return app
