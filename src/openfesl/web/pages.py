import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from babel import Locale
from babel.dates import format_skeleton, format_timedelta
from babel.lists import format_list
from babel.numbers import format_decimal
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

from openfesl.core.admins import Admin
from openfesl.core.census import Census
from openfesl.core.games import Game
from openfesl.core.texts import languages, localize, translate, translate_count
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
        head: The script that runs before the page is first drawn.
        app: The module with htmx and the pages' motion.
        styles: The stylesheets the module brings.
    """

    head: str = ""
    app: str = ""
    styles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Toast:
    """What came of the admin's last change, shown once on the next page.

    Attributes:
        key: The text in the catalogues.
        values: The text's placeholders.
        error: Whether the change failed; such a toast stays until dismissed.
    """

    key: str
    values: Mapping[str, str]
    error: bool


@dataclass(frozen=True, slots=True)
class Viewer:
    """The admin signed in to the admin panel, as its pages show them.

    Attributes:
        admin: The admin.
        through: The name of the provider they signed in through.
        toast: What came of their last change, if it is still to be shown.
        counts: The numbers the menu shows by its items, such as the servers waiting.
    """

    admin: Admin
    through: str
    toast: Toast | None = None
    counts: Mapping[str, int] = field(default_factory=dict)


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
def _text(context: Context, key: str, /, **values: object) -> str:
    # Like Jinja's i18n extension with new-style gettext, for catalogues whose ids are keys.
    return _fill(context, translate(key, context["language"]), values)


@pass_context
def _count_text(context: Context, key: str, count: int, /, **values: object) -> str:
    # The text's plural form follows the count, which it shows as %(count)s.
    text = translate_count(key, count, context["language"])
    return _fill(context, text, {"count": _number(context, count), **values})


@pass_context
def _number(context: Context, value: float, places: int | None = None) -> str:
    # A number with places keeps that many decimals, as ratios show them. Languages group the
    # digits of whole numbers from a length of their own, as the catalogues say.
    language = context["language"]
    pattern = None if places is None else "#,##0." + "0" * places
    grouped = len(str(abs(int(value)))) >= int(translate("web.number.grouped_from", language))
    return format_decimal(value, pattern, locale=language, group_separator=grouped)


def moment(value: datetime, language: str, day: str = "yMMMd") -> str:
    """Return a day and a time of day in UTC as a language writes them.

    Args:
        value: The moment.
        language: The language code.
        day: The CLDR skeleton of the day, which leaves out the year or not.
    """
    locale = Locale.parse(translate("web.locale", language))
    shown = format_skeleton(day, value, tzinfo=UTC, locale=locale)
    time = format_skeleton("Hm", value, tzinfo=UTC, locale=locale)
    return str(locale.datetime_formats["medium"]).format(time, shown)


@pass_context
def _moment(context: Context, value: datetime, day: str = "yMMMd") -> str:
    return moment(value, context["language"], day)


@pass_context
def _when(context: Context, value: datetime, skeleton: str) -> str:
    # A time in UTC with the fields a CLDR skeleton names, such as MMMEd for "Sat 17 Oct", in the
    # order and words of the page's language.
    return str(format_skeleton(skeleton, value, tzinfo=UTC, locale=_locale(context)))


@pass_context
def _ago(context: Context, value: datetime) -> str:
    # How long ago a moment was, as the page's language says it, such as "18 minutes ago".
    elapsed = value - datetime.now(UTC)
    return str(format_timedelta(elapsed, add_direction=True, locale=_locale(context)))


@pass_context
def _country(context: Context, code: str) -> str:
    # A country's name in the page's language, from its ISO code.
    return str(_locale(context).territories.get(code.upper(), code))


@pass_context
def _language_name(context: Context, code: str) -> str:
    # A language's name in the page's language, from its code.
    return str(Locale.parse(code).get_display_name(_locale(context)))


@pass_context
def _listed(context: Context, items: list[str]) -> str:
    # Names joined as the page's language lists them, such as "A, B and C".
    return str(format_list(items, locale=_locale(context)))


def _locale(context: Context) -> Locale:
    return Locale.parse(translate("web.locale", context["language"]))


def page_links(page: int, pages: int) -> list[int | None]:
    """Return the pages a list links to from one of them, with None where some are left out.

    Lists of up to seven pages link to all of them; longer ones to the first and last pages, the
    pages next to the current one, and the five at either end the current page is close to.

    Args:
        page: The current page, from 1.
        pages: How many pages there are.
    """
    if pages <= 7:
        return list(range(1, pages + 1))
    shown = {1, page - 1, page, page + 1, pages}
    if page <= 4:
        shown |= set(range(1, 6))
    if page >= pages - 3:
        shown |= set(range(pages - 4, pages + 1))
    links: list[int | None] = []
    for number in sorted(shown & set(range(1, pages + 1))):
        if links and number - (links[-1] or 0) > 1:
            links.append(None)
        links.append(number)
    return links


def _fill(context: Context, text: str, values: Mapping[str, object]) -> str:
    if context.eval_ctx.autoescape:
        text = escape(text)
    return text % values if values else text


_catalog = Catalog(
    jinja_env=Environment(autoescape=True, undefined=StrictUndefined),
    globals={
        "_": _text,
        "_n": _count_text,
        "number": _number,
        "static": static_url,
        "page_links": page_links,
        "moment": _moment,
        "when": _when,
        "ago": _ago,
        "listed": _listed,
        "country": _country,
        "language_name": _language_name,
    },
)
# The catalogue keeps its own environment, which takes the given one's escaping but not its
# whitespace control.
_catalog.jinja_env.trim_blocks = True
_catalog.jinja_env.lstrip_blocks = True
_catalog.add_folder(Path(__file__).with_name("components"))
_catalog.add_folder(Path(__file__).with_name("templates"), prefix="page")


class Pages:
    """Renders the web pages in the OpenFESL shell, in the language their address names."""

    def __init__(self, games: Iterable[Game], census: Census) -> None:
        """Prepare the shell for the games the server serves.

        Args:
            games: Every game, listed in the shell with its players online and dressed in its
                theme.
            census: Counts the players of each game.
        """
        self._games = list(games)
        self._census = census
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
        viewer: Viewer | None = None,
        csrf: str = "",
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
            viewer: The admin signed in to the admin panel, on its pages.
            csrf: The token that the admin panel's forms send back.
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
            viewer=viewer,
            csrf=csrf,
            # The time the page's values are from.
            now=datetime.now(UTC),
            games=[
                {
                    "name": entry.name,
                    "href": localize(f"/{entry.id}", language),
                    "current": entry is game,
                    "online": self._census.activity(entry).online,
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
    tiers = values.pop("tiers")
    properties = [f'--area-font:"{values.pop("font")}","Barlow",system-ui,sans-serif']
    properties += [f"--{name.replace('_', '-')}:{value}" for name, value in values.items()]
    scope = f'[data-game="{game.id}"]'
    rules = [f"{scope}{{{';'.join(properties)}}}"]
    # An earned insignia names its tier, since the pages' policy allows no inline styles.
    rules += [
        f'{scope} [data-tier="{tier}"]{{--mark:{fill};--mark-ink:{ink}}}'
        for tier, (fill, ink) in enumerate(tiers, 1)
    ]
    return "".join(f"{rule}\n" for rule in rules)


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
    return Assets(
        head=base + entries["head"]["file"],
        app=base + entries["app"]["file"],
        styles=tuple(base + name for name in entries["app"].get("css", [])),
    )


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:12]
