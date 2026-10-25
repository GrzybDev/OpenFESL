import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, StrictUndefined, pass_context
from jinja2.runtime import Context
from jinjax import Catalog
from markupsafe import escape
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from openfesl.core.games import Game
from openfesl.core.texts import languages, localize, translate
from openfesl.web.languages import split_language
from openfesl.web.security import HEADERS

STATIC = Path(__file__).with_name("static")
# Vite writes the built scripts, stylesheet and fonts here, named after their content.
BUILT = STATIC / "build"
MANIFEST = BUILT / "manifest.json"
# A static file's address names its version, so browsers may keep it until the file changes.
KEEP = "public, max-age=31536000, immutable"


@dataclass(frozen=True)
class Assets:
    """The addresses of the built files every page loads.

    Attributes:
        styles: The stylesheets of the pages' entry.
    """

    styles: tuple[str, ...] = ()


def built_assets() -> Assets:
    """Return the built files' addresses, from the manifest of the latest build."""
    try:
        modified = MANIFEST.stat().st_mtime_ns
    except FileNotFoundError:
        # The tests run without a build, so pages then load no scripts or stylesheets.
        return Assets()
    return _read_manifest(modified)


def static_url(name: str) -> str:
    """Return the address of one of OpenFESL's own static files, versioned by its content.

    Args:
        name: The file's path in the static folder, such as icons.svg.
    """
    path = STATIC / name
    return f"/static/{name}?v={_version(path, path.stat().st_mtime_ns)}"


@pass_context
def _text(context: Context, key: str, **values: object) -> str:
    # Like Jinja's i18n extension with new-style gettext, for catalogues whose ids are keys.
    text = translate(key, context["language"])
    if context.eval_ctx.autoescape:
        text = escape(text)
    return text % values if values else text


_catalog = Catalog(
    jinja_env=Environment(autoescape=True, undefined=StrictUndefined),
    globals={"_": _text, "static": static_url},
)
# The catalogue keeps its own environment, which takes the given one's escaping but not its
# whitespace control.
_catalog.jinja_env.trim_blocks = True
_catalog.jinja_env.lstrip_blocks = True
_catalog.add_folder(Path(__file__).with_name("components"))
_catalog.add_folder(Path(__file__).with_name("templates"), prefix="page")


class Pages:
    """Renders the web pages in the OpenFESL shell, in the language their address names."""

    def __init__(self, games: Iterable[Game]) -> None:
        """Prepare the shell for the games the server serves.

        Args:
            games: Every game, listed in the shell and dressed in its theme.
        """
        self._games = list(games)
        self.themes = "".join(_theme_rule(game) for game in self._games)
        self._themes_url = f"/themes.css?v={_digest(self.themes.encode())}"

    def render(
        self,
        request: Request,
        template: str,
        status: int = 200,
        *,
        game: Game | None = None,
        steel: bool = False,
        switch: bool = True,
        headers: Mapping[str, str] | None = None,
        **context: Any,
    ) -> HTMLResponse:
        """Render a page in the shell.

        Args:
            request: The request the page answers.
            template: The page's component, such as Servers.
            status: The response's status code.
            game: The game whose area the page belongs to, dressed in the game's theme.
            steel: Whether the area wears OpenFESL's own steel instead of the shell's grey.
            switch: Whether the shell offers the page in the other languages.
            headers: Headers of the response.
            context: The template's own values.
        """
        language, path = _address(request)
        query = f"?{request.url.query}" if request.url.query else ""
        shared = dict(
            language=language,
            url=lambda target: localize(target, language),
            here=localize(path, language) + query,
            assets=built_assets(),
            themes=self._themes_url,
            game=game,
            steel=steel,
            games=[
                {
                    "name": entry.name,
                    "href": localize(f"/{entry.id}", language),
                    "current": entry is game,
                }
                for entry in self._games
            ],
            languages=[
                {
                    "code": code,
                    "name": translate("web.language.name", code),
                    "href": localize(path, code) + query,
                    "current": code == language,
                }
                for code in languages()
            ]
            if switch
            else [],
        )
        page = _catalog.render(f"page:{template}", **context, _globals=shared)
        return HTMLResponse(page, status, headers)

    async def not_found(self, request: Request, error: HTTPException) -> Response:
        """Answer an address without a page with the not-found page; other errors as usual.

        Args:
            request: The request.
            error: The HTTP error a route or the router raised.
        """
        if error.status_code != 404:
            return await http_exception_handler(request, error)
        return self.render(request, "Error", 404, code=404)

    async def failure(self, request: Request, error: Exception) -> Response:
        """Answer a request that failed with the short error page, without the error's details.

        Args:
            request: The request.
            error: What went wrong; the server logs it.
        """
        # This page is drawn outside the middleware that adds the headers to every other one.
        return self.render(request, "Error", 500, headers=HEADERS, code=500)


class StaticAssets(StaticFiles):
    """Serves the static files; browsers keep those whose address or name holds a version."""

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        """Answer a static file, cached for good when its address or built name holds its version.

        Args:
            full_path: The file.
            stat_result: The file's status.
            scope: The ASGI connection scope.
            status_code: The response's status code.
        """
        response = super().file_response(full_path, stat_result, scope, status_code)
        built = Path(full_path).is_relative_to(BUILT / "assets")
        if built or "v" in QueryParams(scope["query_string"]):
            response.headers["Cache-Control"] = KEEP
        return response


def _address(request: Request) -> tuple[str, str]:
    state = request.scope.get("state", {})
    if "language" in state:
        return state["language"], request.url.path
    # The server's error page sees the address before the language middleware strips it.
    return split_language(request.url.path)


def _theme_rule(game: Game) -> str:
    values = game.theme.model_dump()
    properties = [f'--area-font:"{values.pop("font")}","Barlow",system-ui,sans-serif']
    properties += [f"--{name.replace('_', '-')}:{value}" for name, value in values.items()]
    return f'[data-game="{game.id}"]{{{";".join(properties)}}}\n'


# The modification time is part of the key, so that a rebuilt file gets a new version.
@cache
def _version(path: Path, modified: int) -> str:
    return _digest(path.read_bytes())


@cache
def _read_manifest(modified: int) -> Assets:
    entries = {
        entry["name"]: entry
        for entry in json.loads(MANIFEST.read_text(encoding="utf-8")).values()
        if entry.get("isEntry")
    }
    base = "/static/build/"
    return Assets(styles=tuple(base + name for name in entries["app"].get("css", [])))


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:12]
