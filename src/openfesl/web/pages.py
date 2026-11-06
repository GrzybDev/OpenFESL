import hashlib
import json
import os
import re
from base64 import b64encode
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from math import ceil
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
from openfesl.core.game_art import GameArt
from openfesl.core.games import Game
from openfesl.core.hero_rules import HeroRules, SalePrice
from openfesl.core.texts import languages, localize, translate, translate_count
from openfesl.web.languages import split_language
from openfesl.web.mugshots import mugshot_url
from openfesl.web.security import HEADERS
from openfesl.web.sessions import admin_signed_in, form_token, once_token

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
    """What came of the last change, shown once on the next page.

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
        counts: The numbers the menu shows by its items, such as the servers waiting.
        sections: The sections of the panel that the served games add to its menu, such as
            catalogues.
    """

    admin: Admin
    through: str
    counts: Mapping[str, int] = field(default_factory=dict)
    sections: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Player:
    """The account signed in on the site's pages, as the shell's account menu shows it.

    Attributes:
        account_id: The account's number.
        name: Its public display name, once it has one.
        way: The name of the service it signed in through; empty for the e-mail address.
    """

    account_id: int
    name: str | None
    way: str


def built_assets() -> Assets:
    """Return the built files' addresses, from the manifest of the latest build."""
    try:
        modified = MANIFEST.stat().st_mtime_ns
    except FileNotFoundError:
        # Tests may run without a build, so pages then load no scripts or stylesheets.
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
    return number(value, context["language"], places)


def number(value: float, language: str, places: int | None = None) -> str:
    """Return a number as a language writes it.

    Languages group the digits of whole numbers from a length of their own, as the catalogues
    say.

    Args:
        value: The number.
        language: The language code.
        places: The decimals to keep, as ratios show them; as many as it has when omitted.
    """
    pattern = None if places is None else "#,##0." + "0" * places
    grouped = len(str(abs(int(value)))) >= int(translate("web.number.grouped_from", language))
    return format_decimal(value, pattern, locale=language, group_separator=grouped)


def whole_number(text: str, most: int, least: int = 1) -> int | None:
    """Return the whole number from least to most that a form's field holds, if it holds one.

    The field may group the digits as number() writes them in any language.

    Args:
        text: The field's text.
        most: The highest number the field takes.
        least: The lowest number the field takes.
    """
    digits = "".join(text.replace(",", "").split())
    # More digits than most has are too many, and int() refuses thousands of them outright.
    if not (digits.isascii() and digits.isdecimal()) or len(digits) > len(str(most)):
        return None
    return int(digits) if least <= int(digits) <= most else None


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
def _cost(context: Context, price: int, currency: str, sale: SalePrice | None) -> str:
    # A price with its currency, then the day its sale ends and the usual price after it.
    if sale is None:
        return f"{_number(context, price)} {currency}"
    return _text(
        context,
        "web.sale.cost",
        price=_number(context, price),
        currency=currency,
        end=_when(context, sale.ends, "MMMMd"),
        usual=_number(context, sale.usual),
    )


@pass_context
def _ago(context: Context, value: datetime) -> str:
    # How long ago a moment was, as the page's language says it, such as "18 minutes ago".
    elapsed = value - datetime.now(UTC)
    return str(format_timedelta(elapsed, add_direction=True, locale=_locale(context)))


@pass_context
def _seconds_left(context: Context, ends: datetime) -> int:
    # The whole seconds from the page's time to a moment, rounded up, which its countdown counts
    # down from: the browser's clock may be ahead of the server's or behind it.
    now: datetime = context["now"]
    return max(ceil((ends - now).total_seconds()), 0)


@pass_context
def _country(context: Context, code: str) -> str:
    # A country's name in the page's language, from its ISO code.
    return str(_locale(context).territories.get(code.upper(), code))


def language_name(code: str, language: str) -> str:
    """Return a language's name as another language writes it.

    Args:
        code: The named language's code, such as pt-BR.
        language: The code of the language it is written in.
    """
    locale = Locale.parse(translate("web.locale", language))
    return str(Locale.parse(code, sep="-").get_display_name(locale))


@pass_context
def _language_name(context: Context, code: str) -> str:
    return language_name(code, context["language"])


def listed(items: list[str], language: str) -> str:
    """Return names joined as a language lists them, such as "A, B and C".

    Args:
        items: The names.
        language: The language code.
    """
    return str(format_list(items, locale=Locale.parse(translate("web.locale", language))))


@pass_context
def _listed(context: Context, items: list[str]) -> str:
    return listed(items, context["language"])


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


# The browser that games show their pages in predates SVG inside HTML but draws SVG images, so
# those pages draw each glyph as an image in its colours.
@cache
def _glyph(name: str, size: int, stroke: str = "", fill: str = "") -> str:
    box, body = _symbols()[name]
    paint = (f' stroke="{stroke}"' if stroke else "") + (f' fill="{fill}"' if fill else "")
    image = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="{box}">'
        f"<g{paint}>{body}</g></svg>"
    )
    return f"data:image/svg+xml;base64,{b64encode(image.encode()).decode()}"


@cache
def _symbols() -> dict[str, tuple[str, str]]:
    # The sprite is OpenFESL's own file, one symbol per element as written there.
    sprite = (STATIC / "icons.svg").read_text("utf-8")
    pattern = re.compile(r'<symbol id="([^"]+)" viewBox="([^"]+)">(.*?)</symbol>', re.DOTALL)
    return {name: (box, body) for name, box, body in pattern.findall(sprite)}


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
        "once_token": once_token,
        "static": static_url,
        "mugshot": mugshot_url,
        "page_links": page_links,
        "glyph": _glyph,
        "moment": _moment,
        "when": _when,
        "cost": _cost,
        "ago": _ago,
        "seconds_left": _seconds_left,
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


def render_alone(
    template: str,
    language: str,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
    **context: Any,
) -> HTMLResponse:
    """Render a page outside the shell, such as one that a game shows in its own browser.

    Args:
        template: The page's component, such as ingame.Store.
        language: The language of its texts.
        status: The response's status code.
        headers: Headers of the response.
        context: The template's own values.
    """
    shared = {"language": language, "now": datetime.now(UTC)}
    page = _catalog.render(f"page:{template}", **context, _globals=shared)
    return HTMLResponse(page, status, headers)


class Pages:
    """Renders the web pages in the OpenFESL shell, in the language their address names."""

    def __init__(
        self, games: Iterable[Game], census: Census, art: GameArt, sign_in_check: bool
    ) -> None:
        """Prepare the shell for the games the server serves.

        Args:
            games: Every game, listed in the shell with its players online and dressed in its
                theme.
            census: Counts the players of each game.
            art: Says to whom each game's art is shown.
            sign_in_check: Whether the sign-in with an e-mail address asks for Cloudflare
                Turnstile's check, which only the sign-in pages load, so that the pages of a
                game's own site link to them instead of taking the password.
        """
        self._games = list(games)
        self._census = census
        self._art = art
        self._sign_in_check = sign_in_check
        self.themes = "".join(_theme_rule(game) for game in self._games)
        self._themes_url = f"/themes.css?v={_digest(self.themes.encode())}"

    def shows_art(self, request: Request, game: Game) -> bool:
        """Return whether a page shows a game's imported files and portraits to its viewer.

        Args:
            request: The page's request, whose sessions say whether its viewer is signed in.
            game: The game.
        """
        player = request.scope.get("state", {}).get("player")
        return self._art.shown(game, player is not None or admin_signed_in(request))

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
            csrf: The token that the page's forms send back; the session's when empty, as for
                the account menu's sign-out.
            context: The template's own values.
        """
        language, path = _address(request)
        session = request.scope.get("session") or {}
        state = request.scope.get("state", {})
        player: Player | None = state.get("player")
        # A game's own site offers its sign-in form on every page to a visitor not signed in,
        # unless the sign-in asks for the check or the request failed before its session was read.
        form = "session" in request.scope and not self._sign_in_check
        if game and game.site and not player and not csrf and form:
            csrf = form_token(request)
        query = f"?{request.url.query}" if request.url.query else ""
        shared = dict(
            language=language,
            url=lambda target: localize(target, language),
            here=localize(path, language) + query,
            # The page's own address, without its language and query.
            path=path,
            bar=state.get("site_bar"),
            sign_in_check=self._sign_in_check,
            # The Daily Claw that a game's own site lists, for a visitor as for an account.
            claw=state["site_bar"].claw if state.get("site_bar") else state.get("claw"),
            assets=built_assets(),
            themes=self._themes_url,
            game=game,
            steel=steel,
            viewer=viewer,
            player=player,
            # Whether the page shows a game's art to its viewer, or placeholders instead.
            shows_art=lambda shown: self.shows_art(request, shown),
            csrf=csrf or session.get("csrf", ""),
            toast=_take_toast(session) if player or viewer else None,
            # The time the page's values are from.
            now=datetime.now(UTC),
            games=[
                {
                    "name": entry.name,
                    "href": localize(f"/{entry.id}", language),
                    "pages": entry.pages,
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
        return self.render(request, "Error", 404, game=self._site_of(request), code=404)

    async def failure(self, request: Request, error: Exception) -> Response:
        """Answer a request that failed with the short error page, without the error's details.

        Args:
            request: The request.
            error: What went wrong; the server logs it.
        """
        # This page is drawn outside the middleware that adds the headers to every other one.
        game = self._site_of(request)
        return self.render(request, "Error", 500, game=game, headers=HEADERS, code=500)

    def _site_of(self, request: Request) -> Game | None:
        # The game with a site of its own whose pages the address lies under, if any.
        _, path = _address(request)
        first = path.split("/")[1]
        return next((game for game in self._games if game.site and game.id == first), None)


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


def _take_toast(session: dict[str, Any]) -> Toast | None:
    shown = session.pop("toast", None)
    return Toast(shown["key"], shown["values"], shown["error"]) if shown else None


def _address(request: Request) -> tuple[str, str]:
    state = request.scope.get("state", {})
    # The server's error page sees the address before the language middleware strips it.
    language, path = (
        (state["language"], request.url.path)
        if "language" in state
        else split_language(request.url.path)
    )
    # An address that opens with // would make the page's links lead to another site.
    return language, "/" + path.lstrip("/")


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
    rules += [
        f'{scope} [data-wallet="{wallet.currency}"]{{--coin:{wallet.fill};--coin-ink:{wallet.ink}}}'
        for wallet in (game.site.wallets if game.site else [])
    ]
    if game.heroes:
        rules += _hero_rules(scope, game.heroes)
    return "".join(f"{rule}\n" for rule in rules)


def _hero_rules(scope: str, heroes: HeroRules) -> list[str]:
    # The colours of each faction, and of every skin tone and hair colour, for the elements that
    # name them in their attributes.
    rules = []
    for name, faction in heroes.factions.items():
        colours = {
            "faction": faction.colour,
            "faction-ink": faction.ink,
        }
        listed = ";".join(f"--{key}:{value}" for key, value in colours.items())
        rules.append(f'{scope} [data-faction="{name}"]{{{listed}}}')
    rules += [
        f'{scope} [data-skin="{tone}"]{{--skin:{colour}}}'
        for tone, colour in enumerate(heroes.skin_tones, 1)
    ]
    rules += [
        f'{scope} [data-hair-colour="{number}"]{{--hair:{colour}}}'
        for number, colour in enumerate(heroes.hair_colours, 1)
    ]
    return rules


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
