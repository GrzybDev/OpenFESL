from starlette.datastructures import MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Every script, style and font comes from this server, so an injected tag cannot run, and no
# other site may frame the pages.
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def allow(response: Response, directive: str, source: str) -> None:
    """Widen a directive of a page's policy to another source, adding the directive if needed.

    Args:
        response: The page.
        directive: The directive, such as script-src.
        source: The source it allows too, such as an origin.
    """
    policy = response.headers.get("Content-Security-Policy", HEADERS["Content-Security-Policy"])
    parts = policy.split("; ")
    found = [index for index, part in enumerate(parts) if part.split(" ", 1)[0] == directive]
    if found:
        parts[found[0]] += f" {source}"
    else:
        parts.append(f"{directive} {source}")
    response.headers["Content-Security-Policy"] = "; ".join(parts)


class SecurityHeadersMiddleware:
    """Adds the security headers to every response that does not set them itself."""

    def __init__(self, app: ASGIApp) -> None:
        """Put the headers on an application's responses.

        Args:
            app: The application that serves the pages.
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer a request with the headers added.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self._app(scope, receive, send_with_headers)
