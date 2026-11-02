import asyncio
from pathlib import PurePosixPath

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import (
    KEPT_FILE,
    REVISION,
    Catalogue,
    CatalogueFiles,
    Catalogues,
    file_path,
)
from openfesl.core.game_art import GameArt
from openfesl.core.games import Games
from openfesl.core.magma import Magma
from openfesl.formats.icons import png
from openfesl.web.game_art import art_cache, hidden

# Where the files of a game's imports are served, by game, import and path in the import's folder.
CATALOGUE_FILE_PATH = "/catalogues/{game}/{revision}/{path:path}"
_TYPES = {".webp": "image/webp", ".png": "image/png", ".glb": "model/gltf-binary"}


def catalogue_file_url(game: str, revision: str, path: str) -> str:
    """Return the address of a file that an import keeps.

    Args:
        game: The game's data-file name.
        revision: The import.
        path: The file's path in the import's folder.
    """
    return f"/catalogues/{game}/{revision}/{path}"


def icon_url(
    game: str, catalogue: Catalogue, item_id: int, shown: bool, as_png: bool = False
) -> str | None:
    """Return the address of an item's imported icon, for a page that shows the game's art.

    Args:
        game: The game's data-file name.
        catalogue: The game's imported catalogue.
        item_id: The item's number.
        shown: Whether the page shows the game's art to its viewer.
        as_png: Whether the icon is wanted as a PNG picture, for a browser without WebP.

    Returns:
        None where the art is hidden, the import keeps no icons, or the item has none or is
        withheld.
    """
    contents, item = catalogue.contents, catalogue.item(item_id)
    revision = contents.revision if shown and contents.icons else None
    if revision is None or item is None or not item.icon or item.withheld:
        return None
    path = PurePosixPath(file_path("icons", item.icon))
    return catalogue_file_url(game, revision, str(path.with_suffix(".png") if as_png else path))


def catalogue_file_routes(
    games: Games, catalogues: Catalogues, files: CatalogueFiles, art: GameArt, magma: Magma
) -> APIRouter:
    """Return the icons, hero models and textures that the games' imported catalogues keep.

    They are served to whom the game's art is shown. The icons of withheld items are left out,
    as only the admin panel shows them. An icon asked for as a PNG picture is made from the
    import's own once and kept beside it.

    Args:
        games: The games, whose catalogues name the files.
        catalogues: The games' imported catalogues, which say what items are withheld.
        files: Where the files are kept.
        art: Says to whom each game's art is shown.
        magma: Recognises the games' sessions, which count as signed in.
    """
    router = APIRouter()

    @router.get(CATALOGUE_FILE_PATH)
    async def catalogue_file(request: Request, game: str, revision: str, path: str) -> Response:
        known = games.by_id(game)
        if known is None or not (REVISION.fullmatch(revision) and KEPT_FILE.fullmatch(path)):
            raise HTTPException(404)
        source = str(PurePosixPath(path).with_suffix(".webp")) if path.endswith(".png") else path
        items = catalogues.contents(known).items
        withheld = known.catalogue.withheld if known.catalogue else frozenset()
        icons = {items[number].icon for number in withheld if number in items}
        if source in {file_path("icons", icon) for icon in icons if icon}:
            raise HTTPException(404)
        cache = art_cache(request, known, art, magma)
        if cache is None:
            return hidden()
        located = await files.located(known.id, revision, path)
        if located is None and source != path:
            kept = await files.located(known.id, revision, source)
            if kept is not None:
                made = await asyncio.to_thread(png, await asyncio.to_thread(kept.read_bytes))
                await files.write(known.id, revision, {path: made})
                located = await files.located(known.id, revision, path)
        if located is None:
            raise HTTPException(404)
        return FileResponse(
            located, media_type=_TYPES[located.suffix], headers={"Cache-Control": cache}
        )

    return router
