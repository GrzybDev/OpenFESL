from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from openfesl.core.game_art import GameArt
from openfesl.core.games import Games
from openfesl.core.magma import Magma
from openfesl.core.mugshots import Mugshots
from openfesl.web.game_art import art_cache, hidden

# Where a hero's mugshot is served, by game, hero and version; outside the games' sites, so that
# a page's pictures do not each read the site's bar.
MUGSHOT_PATH = "/mugshots/{game}/{hero_id}/{version}.png"


def mugshot_url(game: str, hero_id: int, version: int) -> str:
    """Return the address of a version of a hero's mugshot.

    Args:
        game: The game's data-file name.
        hero_id: The hero's persona.
        version: The mugshot's version.
    """
    return MUGSHOT_PATH.format(game=game, hero_id=hero_id, version=version)


def mugshot_routes(games: Games, mugshots: Mugshots, art: GameArt, magma: Magma) -> APIRouter:
    """Return the mugshots that games rendered of their heroes, each version at its own address.

    They are the game's art, served to whom the game's art is shown.

    Args:
        games: The games, whose heroes the mugshots show.
        mugshots: Where the mugshots are kept.
        art: Says to whom each game's art is shown.
        magma: Recognises the games' sessions, which count as signed in.
    """
    router = APIRouter()

    @router.get(MUGSHOT_PATH)
    async def mugshot(request: Request, game: str, hero_id: int, version: int) -> Response:
        known = games.by_id(game)
        if known is None:
            raise HTTPException(404)
        cache = art_cache(request, known, art, magma)
        if cache is None:
            return hidden()
        found = await mugshots.image(known, hero_id)
        if found is None or found[1] != version:
            raise HTTPException(404)
        return Response(found[0], media_type="image/png", headers={"Cache-Control": cache})

    return router
