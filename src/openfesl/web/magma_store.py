import asyncio
import gzip

from fastapi import APIRouter, Request
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.store import Offer, Store
from openfesl.protocol.magma import ProductEntry, products
from openfesl.web.magma import XML


def store_router(game: Game, store: Store) -> APIRouter:
    """Return the routes of a game's store on its Magma host.

    Args:
        game: The game whose store they serve.
        store: The games' stores.
    """
    router = APIRouter()
    # The store's list, plain and compressed, as of a count of the store's changes: the game and
    # its servers load it with each hero, and a whole store runs to megabytes.
    built: tuple[int, bytes, bytes] | None = None

    @router.get("/ofb/products")
    async def offers(request: Request) -> Response:
        nonlocal built
        if built is None or built[0] != store.changes:
            changes = store.changes
            entries = [_entry(offer) for offer in await store.offers(game)]
            # Built away from the loop, which would hold up the relay's games meanwhile.
            plain = await asyncio.to_thread(products, entries)
            built = changes, plain, await asyncio.to_thread(gzip.compress, plain)
        _, plain, packed = built
        if "gzip" in request.headers.get("Accept-Encoding", ""):
            return Response(packed, media_type=XML, headers={"Content-Encoding": "gzip"})
        return Response(plain, media_type=XML)

    # No hot deals yet, while a game still needs the list to load a persona.
    @router.get("/{language}/game/hotDeals")
    async def hot_deals() -> Response:
        return Response(products(), media_type=XML)

    return router


def _entry(offer: Offer) -> ProductEntry:
    product = offer.product
    return ProductEntry(
        id=offer.product_id,
        name=offer.name,
        tag=product.ownership.tag(product.item_id),
        group=offer.kind,
        sort=product.sort,
        currency=product.currency,
        price=product.price,
        uses=product.ownership.uses,
        days=product.ownership.days,
        original_price=product.original_price,
    )
