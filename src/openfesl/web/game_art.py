from fastapi import Request
from fastapi.responses import Response

from openfesl.core.game_art import ArtViewers, GameArt
from openfesl.core.games import Game
from openfesl.core.magma import Magma, MagmaError
from openfesl.web.sessions import admin_signed_in

# A file of a game's art never changes, and a new one comes under its own address, so browsers
# keep it for good; shared caches keep it ten minutes, so that a choice to show the art to fewer
# viewers reaches them soon, and while only signed-in viewers see it, none keeps it for others.
PUBLIC_CACHE = "public, max-age=31536000, s-maxage=600, immutable"
PRIVATE_CACHE = "private, max-age=31536000, immutable"


def art_cache(request: Request, game: Game, art: GameArt, magma: Magma) -> str | None:
    """Return how a file of a game's art may be kept, or None where it is not shown to the viewer.

    Players and admins signed in on the site count as signed in, and so does the game through
    the session its magma cookie names.

    Args:
        request: The request for the file.
        game: The game whose art the file is.
        art: Says to whom each game's art is shown.
        magma: Recognises the game's sessions.
    """
    viewers = art.viewers(game)
    if viewers is ArtViewers.EVERYONE:
        return PUBLIC_CACHE
    player = request.scope.get("state", {}).get("player")
    if viewers is ArtViewers.SIGNED_IN and (
        player is not None or admin_signed_in(request) or _game_session(request, magma)
    ):
        return PRIVATE_CACHE
    return None


def hidden() -> Response:
    """Return the answer for a file of art not shown to its viewer, which no cache may keep."""
    # A proxy that kept a visitor's answer would hide the file from signed-in viewers too.
    return Response(status_code=404, headers={"Cache-Control": "no-store"})


def _game_session(request: Request, magma: Magma) -> bool:
    try:
        magma.session(request.cookies.get("magma"))
    except MagmaError:
        return False
    return True
