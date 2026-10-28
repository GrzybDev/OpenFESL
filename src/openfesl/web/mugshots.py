from fastapi import APIRouter
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from openfesl.core.games import Games
from openfesl.core.mugshots import Mugshots

# Where a hero's mugshot is served, by game, hero and version; outside the games' sites, so that
# a page's pictures do not each read the site's bar.
MUGSHOT_PATH = "/mugshots/{game}/{hero_id}/{version}.png"
# A version never changes its picture, and a new one comes under its own address.
MUGSHOT_CACHE = "public, max-age=31536000, immutable"


def mugshot_url(game: str, hero_id: int, version: int) -> str:
    """Return the address of a version of a hero's mugshot.

    Args:
        game: The game's data-file name.
        hero_id: The hero's persona.
        version: The mugshot's version.
    """
    return MUGSHOT_PATH.format(game=game, hero_id=hero_id, version=version)


def mugshot_routes(games: Games, mugshots: Mugshots) -> APIRouter:
    """Return the mugshots that games rendered of their heroes, each version at its own address.

    Args:
        games: The games, whose heroes the mugshots show.
        mugshots: Where the mugshots are kept.
    """
    router = APIRouter()

    @router.get(MUGSHOT_PATH)
    async def mugshot(game: str, hero_id: int, version: int) -> Response:
        known = games.by_id(game)
        found = await mugshots.image(known, hero_id) if known else None
        if found is None or found[1] != version:
            raise HTTPException(404)
        return Response(found[0], media_type="image/png", headers={"Cache-Control": MUGSHOT_CACHE})

    return router
