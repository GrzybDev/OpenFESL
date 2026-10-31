import asyncio
import re
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from PIL import Image
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import CatalogueFiles, Catalogues, file_path
from openfesl.core.deal_uploads import DealUploads, offer_of
from openfesl.core.game_art import GameArt
from openfesl.core.games import Game
from openfesl.core.magma import Magma
from openfesl.core.store import ListedBundle, ProductStore
from openfesl.core.texts import BASE_LANGUAGE
from openfesl.formats.deal_art import deal_art
from openfesl.web.game_art import art_cache

# Where the game finds each hot deal's pictures, on its Magma host: plain http, no query and no
# port, as the game turns the address into a file name.
HOT_DEALS = "/static/hotdeals"
_NAME = re.compile(r"(\d{1,18})_(big|small)\.png")
# The pictures kept once made, which the game asks for again on every visit to its home screen.
_KEPT = 256


def deal_art_url(game: Game, number: int, big: bool) -> str:
    """Return the address the game downloads a hot deal's picture from.

    Args:
        game: The game, which has a Magma host.
        number: The deal's bundle number.
        big: Whether it is the big picture rather than the small one.
    """
    return f"http://{game.http.magma}{HOT_DEALS}/{number}_{'big' if big else 'small'}.png"


class DealArt:
    """Makes the pictures of the games' hot deals for their home screens.

    The items show as their imported icons where the game's art is shown to the game, and as
    plain tiles otherwise; a picture once made is kept. A picture that the admin uploaded for a
    deal's offer takes the generated one's place.
    """

    def __init__(
        self,
        products: ProductStore,
        catalogues: Catalogues,
        files: CatalogueFiles | None,
        uploads: DealUploads,
    ) -> None:
        """Read the deals, the catalogues and their files.

        Args:
            products: Hold the bundles, sold now or not.
            catalogues: Know the items' icons.
            files: Where the imports' icons are kept; none where the deployment keeps none.
            uploads: The pictures that the admin uploaded in place of generated ones.
        """
        self._products = products
        self._uploads = uploads
        self._catalogues = catalogues
        self._files = files
        self._kept: OrderedDict[tuple[str, int, bool, str | None], bytes] = OrderedDict()

    async def picture(self, game: Game, number: int, big: bool, shown: bool) -> bytes | None:
        """Return a hot deal's picture as a PNG, or None for a number the store has no bundle by.

        Args:
            game: The game.
            number: The deal's bundle number.
            big: Whether it is the big picture rather than the small one.
            shown: Whether the game's art is shown to the asker, so that imported icons may be.
        """
        listed = await self._products.bundle(game.id, number)
        if listed is None:
            return None
        uploaded = await self._uploads.picture(game.id, offer_of(listed.bundle), big)
        if uploaded is not None:
            return uploaded
        contents = self._catalogues.contents(game)
        revision = contents.revision if shown and contents.icons and self._files else None
        key = (game.id, number, big, revision)
        if (kept := self._kept.get(key)) is not None:
            self._kept.move_to_end(key)
            return kept
        made = await asyncio.to_thread(_made, await self._icons(game, listed, revision), big)
        self._kept[key] = made
        if len(self._kept) > _KEPT:
            self._kept.popitem(last=False)
        return made

    async def _icons(
        self, game: Game, listed: ListedBundle, revision: str | None
    ) -> list[Path | None]:
        # Where each item's icon lies, if it is shown. A random box's item is not known before
        # it is opened, so the box is one plain tile.
        if not listed.members:
            return [None]
        catalogue = await self._catalogues.of(game, BASE_LANGUAGE)
        icons: list[Path | None] = []
        for product in listed.members:
            item = catalogue.item(product.item_id)
            located = None
            if revision and self._files and item and item.icon and not item.withheld:
                path = file_path("icons", item.icon)
                located = await self._files.located(game.id, revision, path)
            icons.append(located)
        return icons


def _made(icons: list[Path | None], big: bool) -> bytes:
    # Reads the icons and makes the picture, away from the server's loop.
    read: list[Image.Image | None] = []
    for located in icons:
        if located is None:
            read.append(None)
            continue
        with Image.open(located) as icon:
            read.append(icon.convert("RGBA"))
    return deal_art(read, big)


def deal_art_router(game: Game, magma: Magma, art: DealArt, game_art: GameArt) -> APIRouter:
    """Return the pictures of a game's hot deals on its Magma host.

    Args:
        game: The game whose deals they show.
        magma: Recognises the game's sessions, to which the game's art may be shown.
        art: Makes the pictures.
        game_art: Says to whom the game's art is shown.
    """
    router = APIRouter()

    # The game saves whatever answers, whatever its status, as the picture.
    @router.get(f"{HOT_DEALS}/{{name}}")
    async def hot_deal_picture(request: Request, name: str) -> Response:
        named = _NAME.fullmatch(name)
        if named is None:
            raise HTTPException(404)
        shown = art_cache(request, game, game_art, magma) is not None
        made = await art.picture(game, int(named[1]), named[2] == "big", shown)
        if made is None:
            raise HTTPException(404)
        return Response(made, media_type="image/png", headers={"Cache-Control": "no-cache"})

    return router
