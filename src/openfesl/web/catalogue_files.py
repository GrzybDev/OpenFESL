from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import KEPT_FILE, REVISION, CatalogueFiles, Catalogues, file_path
from openfesl.core.games import Games

# Where the files of a game's imports are served, by game, import and path in the import's folder.
CATALOGUE_FILE_PATH = "/catalogues/{game}/{revision}/{path:path}"
# An import never changes its files, and a new one comes under its own address.
FILE_CACHE = "public, max-age=31536000, immutable"
_TYPES = {".webp": "image/webp", ".glb": "model/gltf-binary"}


def catalogue_file_url(game: str, revision: str, path: str) -> str:
    """Return the address of a file that an import keeps.

    Args:
        game: The game's data-file name.
        revision: The import.
        path: The file's path in the import's folder.
    """
    return f"/catalogues/{game}/{revision}/{path}"


def catalogue_file_routes(games: Games, catalogues: Catalogues, files: CatalogueFiles) -> APIRouter:
    """Return the icons, hero models and textures that the games' imported catalogues keep.

    The icons of withheld items are left out, as only the admin panel shows them.

    Args:
        games: The games, whose catalogues name the files.
        catalogues: The games' imported catalogues, which say what items are withheld.
        files: Where the files are kept.
    """
    router = APIRouter()

    @router.get(CATALOGUE_FILE_PATH)
    async def catalogue_file(game: str, revision: str, path: str) -> FileResponse:
        known = games.by_id(game)
        if known is None or not (REVISION.fullmatch(revision) and KEPT_FILE.fullmatch(path)):
            raise HTTPException(404)
        items = catalogues.contents(known).items
        withheld = known.catalogue.withheld if known.catalogue else frozenset()
        icons = {items[number].icon for number in withheld if number in items}
        if path in {file_path("icons", icon) for icon in icons if icon}:
            raise HTTPException(404)
        located = await files.located(known.id, revision, path)
        if located is None:
            raise HTTPException(404)
        return FileResponse(
            located,
            media_type=_TYPES[located.suffix],
            headers={"Cache-Control": FILE_CACHE},
        )

    return router
