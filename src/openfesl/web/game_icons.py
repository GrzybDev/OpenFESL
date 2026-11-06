import hashlib
from collections.abc import Awaitable, Callable
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import Response

from openfesl.core.game_icons import GameIcons
from openfesl.core.games import Games
from openfesl.core.rich_presence import PresenceArt
from openfesl.web.sessions import or_404

# Where each game's icon is served, {game} standing for the game's id.
ICON_PATH = "/{game}/icon.png"
# OpenFESL's own icon among the static files, the small image of presence.
SERVICE_ICON = "/static/apple-touch-icon.png"
# Discord's image proxy and browsers keep an icon for an hour, so that one the admin replaces
# shows soon; asking again costs only a 304.
ICON_CACHE = "public, max-age=3600"


def game_icon_routes(games: Games, icons: GameIcons) -> APIRouter:
    """Return each game's icon at its own address, for Discord presence to show.

    Args:
        games: The games, each with its icon's address.
        icons: Where the games' icons are kept.
    """
    router = APIRouter()
    for game in games:
        router.add_api_route(ICON_PATH.format(game=game.id), _icon(icons, game.id))
    return router


def presence_art(public_url: str | None) -> PresenceArt:
    """Return the public addresses of presence's images, none without the server's address.

    Args:
        public_url: The address the server's web pages are reached at.
    """
    if not public_url:
        return PresenceArt()
    base = public_url.rstrip("/")
    return PresenceArt(game=base + ICON_PATH, service=base + SERVICE_ICON)


def icon_version(png: bytes) -> str:
    """Return a digest of an icon that changes with the image, as its entity tag carries it.

    Args:
        png: The icon.
    """
    return hashlib.sha256(png).hexdigest()[:32]


def reaches_discord(public_url: str | None) -> bool:
    """Tell whether Discord can fetch presence's images: only from a public HTTPS address.

    Args:
        public_url: The address the server's web pages are reached at, if the deployment sets it.
    """
    if not public_url:
        return False
    parts = urlsplit(public_url)
    host = parts.hostname or ""
    if parts.scheme != "https" or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        # A host name, which only its DNS records place; taken as public.
        return True


def _icon(icons: GameIcons, game_id: str) -> Callable[[Request], Awaitable[Response]]:
    async def show_icon(request: Request) -> Response:
        png = or_404(await icons.icon(game_id))
        tag = f'"{icon_version(png)}"'
        headers = {"Cache-Control": ICON_CACHE, "ETag": tag}
        asked = request.headers.get("If-None-Match", "")
        if tag in {part.strip().removeprefix("W/") for part in asked.split(",")}:
            return Response(status_code=304, headers=headers)
        return Response(png, media_type="image/png", headers=headers)

    return show_icon
