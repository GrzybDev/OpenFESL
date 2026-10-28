from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi.responses import Response
from markupsafe import Markup

from openfesl.core.games import Game
from openfesl.core.texts import localize
from openfesl.web.in_game import digest, inline, language_of
from openfesl.web.pages import render_alone
from openfesl.web.security import HEADERS

# The windows that the game's menus open with its browser: popup while matchmaking, footer on
# the main menu, loading while a map loads and eor at a round's end (SwiffPlayer.dll 1.89
# 0x10049772), each showing one small notice instead of an ad.
NOTICES = {"popup": True, "footer": False, "loading": True, "eor": True}
# They change with the server's address and texts alone, so the game may keep them for a day.
CACHE = "public, max-age=86400"
# The ad beside the in-game store has the game hide its space (BFHeroes.exe 1.89 0x009fe31e).
HIDE_ADS = "if (window.game && game.showAds) { game.showAds(false); }"


def notices_router(game: Game, public_url: str | None) -> APIRouter:
    """Return the pages that a game's ad windows load from its Magma host, with no ads.

    Args:
        game: The game, whose own site the notices point to.
        public_url: The address the server's web pages are reached at; without it the notices
            name the site without an address.
    """
    router = APIRouter()
    if game.site is None:
        return router
    css = inline("notice.css")
    styles = Markup(f"<style>{css}</style>")
    script = Markup(f"<script>{HIDE_ADS}</script>")
    headers = {
        **HEADERS,
        "Cache-Control": CACHE,
        "Content-Security-Policy": (
            f"default-src 'none'; style-src {digest(css)}; script-src {digest(HIDE_ADS)}; "
            "base-uri 'none'; frame-ancestors 'none'"
        ),
    }
    site = urlsplit(public_url) if public_url else None

    @router.get("/{segment}/game/ingameAd")
    async def beside_store(segment: str) -> Response:
        language = language_of(game, segment)
        return render_alone(
            "ingame.Blank", language, headers=headers, game=game, styles=styles, script=script
        )

    def notice(box: bool) -> Callable[[str], Awaitable[Response]]:
        async def show_notice(segment: str) -> Response:
            language = language_of(game, segment)
            page = localize(f"/{game.id}", language)
            address = f"{site.netloc}{site.path.rstrip('/')}{page}" if site else ""
            return render_alone(
                "ingame.Notice",
                language,
                headers=headers,
                game=game,
                styles=styles,
                address=address,
                box=box,
            )

        return show_notice

    for window, box in NOTICES.items():
        router.add_api_route(f"/{{segment}}/game/{window}", notice(box))
    return router
