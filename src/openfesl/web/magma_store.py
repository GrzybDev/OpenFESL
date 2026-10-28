import asyncio
import gzip
from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.item_writes import ItemWrites
from openfesl.core.magma import Magma
from openfesl.core.purchases import Purchases
from openfesl.core.store import Offer, Store
from openfesl.protocol.magma import ProductEntry, products, success
from openfesl.web.magma import XML, SessionId, UserId

# The id of a product, as the game buys it, and of an item's entitlement, as it changes one.
ProductId = Annotated[str, Path(alias="productId")]
EntitlementId = Annotated[int, Path(alias="entitlementId")]


def store_router(
    game: Game, magma: Magma, store: Store, purchases: Purchases, writes: ItemWrites
) -> APIRouter:
    """Return the routes of a game's store and of the items it sells on its Magma host.

    Args:
        game: The game whose store they serve.
        magma: Answers about who asks.
        store: The games' stores.
        purchases: Sells the stores' products.
        writes: Changes the items the heroes use.
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

    # The game adds what it bought itself once the answer is a success (BFHeroes.exe 1.89
    # 0x007bafb7).
    @router.post("/ofb/purchase/{userId}/{productId}")
    async def purchase(
        persona_id: UserId, product_id: ProductId, session_id: SessionId = None
    ) -> Response:
        persona = await magma.persona(session_id, game, persona_id, own=True)
        await purchases.purchase(game, persona, product_id)
        return Response(success(), media_type=XML)

    # A player's game and the dedicated server keeping the player change the items it uses.
    @router.post("/nucleus/entitlement/{entitlementId}/useCount/{uses}")
    async def use(
        entitlement_id: EntitlementId, uses: int, session_id: SessionId = None
    ) -> Response:
        await _holder(magma, game, writes, entitlement_id, session_id)
        await writes.use_up(entitlement_id, uses)
        return Response(success(), media_type=XML)

    @router.post("/nucleus/entitlement/{entitlementId}/status/{status}")
    async def take_out(
        entitlement_id: EntitlementId, status: str, session_id: SessionId = None
    ) -> Response:
        await _holder(magma, game, writes, entitlement_id, session_id)
        await writes.take_out(entitlement_id, status)
        return Response(success(), media_type=XML)

    # No hot deals yet, while a game still needs the list to load a persona.
    @router.get("/{language}/game/hotDeals")
    async def hot_deals() -> Response:
        return Response(products(), media_type=XML)

    return router


async def _holder(
    magma: Magma, game: Game, writes: ItemWrites, entitlement_id: int, session_id: str | None
) -> None:
    # Who asks first, so that only an asker who may change items learns which exist.
    magma.session(session_id)
    await magma.persona(session_id, game, await writes.holder(entitlement_id))


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
