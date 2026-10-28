from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException

from openfesl.core.games import Games
from openfesl.core.texts import BASE_LANGUAGE, localize
from openfesl.web.languages import preferred_language


def open_routes(games: Games) -> APIRouter:
    """Return the addresses of the games' websites that the games open in the system browser.

    OpenFESLHook sends such an address to /open/<host>/<path>, which opens the game's page for it
    in the language of the address, or else of the browser, since accounts keep no language.

    Args:
        games: The games, of which those with a website of their own name its host.
    """
    router = APIRouter()
    sites = {game.site.host.lower(): game for game in games if game.site and game.site.host}

    @router.get("/open/{host}/{path:path}")
    async def open_address(request: Request, host: str, path: str) -> RedirectResponse:
        game = sites.get(host.lower())
        if game is None or game.site is None:
            raise HTTPException(404)
        page = f"/{game.id}" + game.site.addresses.get(f"/{path}", "")
        language = request.state.language
        if language == BASE_LANGUAGE:
            language = preferred_language(request.headers.get("accept-language", ""))
        # The answer depends on the browser, so no shared cache may keep it for others.
        headers = {"Cache-Control": "private, no-store"}
        return RedirectResponse(localize(page, language), 302, headers)

    return router
