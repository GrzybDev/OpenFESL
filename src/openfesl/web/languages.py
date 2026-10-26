from starlette.datastructures import Headers
from starlette.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.texts import BASE_LANGUAGE, languages, localize


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


def _preferred_language(accept_language: str) -> str:
    """Return the language of the pages that a browser ranks highest, English when none.

    Args:
        accept_language: The browser's Accept-Language header, such as pl-PL,pl;q=0.9,en;q=0.8.
    """
    ranked: list[tuple[float, int, str]] = []
    for position, entry in enumerate(accept_language.split(",")):
        tag, *parameters = (part.strip() for part in entry.split(";"))
        quality = 1.0
        for parameter in parameters:
            name, _, value = parameter.partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        ranked.append((-quality, position, tag.split("-")[0].lower()))
    known = languages()
    # A quality of 0 marks a language the browser does not accept.
    return next(
        (code for rank, _, code in sorted(ranked) if rank < 0 and code in known), BASE_LANGUAGE
    )


class LanguageMiddleware:
    """Reads the language from the start of a page's path, so that each page has one route.

    The application sees the path without the prefix and the language in the request's state.
    A visitor who opens an English address outside the admin panel from outside the site, such
    as from a link or a bookmark, is sent to the page in the language their browser prefers;
    links within the site, such as the language switch, keep the language they name.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Put the language prefixes in front of an application.

        Args:
            app: The application that serves the pages.
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass a request on without its language prefix, or send an arriving visitor on.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        if scope["type"] == "http":
            language, path = split_language(scope["path"])
            if language == BASE_LANGUAGE and (arriving := _arriving_language(scope, path)):
                query = scope["query_string"].decode("latin-1")
                target = localize(path, arriving) + (f"?{query}" if query else "")
                # The answer depends on the browser, so no shared cache may keep it for others.
                headers = {"Cache-Control": "private, no-store"}
                await RedirectResponse(target, 302, headers)(scope, receive, send)
                return
            state = {**scope.get("state", {}), "language": language}
            # A copy, so that the server's error page outside this middleware sees the address
            # as it was asked for.
            scope = {**scope, "path": path, "state": state}
            raw_path: bytes | None = scope.get("raw_path")
            if language != BASE_LANGUAGE and raw_path is not None:
                scope["raw_path"] = raw_path[len(language) + 1 :] or b"/"
        await self._app(scope, receive, send)


def _arriving_language(scope: Scope, path: str) -> str | None:
    # Browsers name a page opened by hand or from another site in Sec-Fetch-Site; without the
    # header, as from the game, the dedicated servers or scripts, nothing is redirected. The
    # admin panel carries its own language through a sign-in, whose return from the provider
    # arrives from another site.
    headers = Headers(scope=scope)
    arriving = (
        scope["method"] == "GET"
        and path != "/admin"
        and not path.startswith("/admin/")
        and headers.get("sec-fetch-dest") == "document"
        and headers.get("sec-fetch-site") in ("none", "cross-site")
    )
    preferred = _preferred_language(headers.get("accept-language", "")) if arriving else None
    return preferred if preferred != BASE_LANGUAGE else None
