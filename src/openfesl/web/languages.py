from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.texts import BASE_LANGUAGE, languages


def split_language(path: str) -> tuple[str, str]:
    """Return the language that a page's address names, and the address without it.

    Args:
        path: The path of a request, such as /pl/terms.
    """
    for language in languages():
        prefix = f"/{language}"
        if language != BASE_LANGUAGE and (path == prefix or path.startswith(prefix + "/")):
            return language, path.removeprefix(prefix) or "/"
    return BASE_LANGUAGE, path


class LanguageMiddleware:
    """Reads the language from the start of a page's path, so that each page has one route.

    The application sees the path without the prefix and the language in the request's state.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Put the language prefixes in front of an application.

        Args:
            app: The application that serves the pages.
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass a request on without its language prefix.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        if scope["type"] == "http":
            language, path = split_language(scope["path"])
            state = {**scope.get("state", {}), "language": language}
            # A copy, so that the server's error page outside this middleware sees the address
            # as it was asked for.
            scope = {**scope, "path": path, "state": state}
            raw_path: bytes | None = scope.get("raw_path")
            if language != BASE_LANGUAGE and raw_path is not None:
                scope["raw_path"] = raw_path[len(language) + 1 :] or b"/"
        await self._app(scope, receive, send)
