import asyncio
import gzip
from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import Response

from openfesl.core.deals import Deal, HotDeals
from openfesl.core.games import Game
from openfesl.core.item_writes import ItemWrites
from openfesl.core.magma import Magma
from openfesl.core.purchases import Purchases
from openfesl.core.store import Offer, Store
from openfesl.protocol.magma import (
    BundleEntry,
    ProductEntry,
    hot_deals,
    nothing_refunded,
    products,
    refunded,
    success,
)
from openfesl.web.deal_art import deal_art_url
from openfesl.web.magma import XML, RelayToken, SessionId, UserId, xml

# The id of a product, as the game buys it, and of an item's entitlement, as it changes one.
ProductId = Annotated[str, Path(alias="productId")]
EntitlementId = Annotated[int, Path(alias="entitlementId")]


def store_router(
    game: Game,
    magma: Magma,
    store: Store,
    deals: HotDeals,
    purchases: Purchases,
    writes: ItemWrites,
) -> APIRouter:
    """Return the routes of a game's store and of the items it sells on its Magma host.

    Args:
        game: The game whose store they serve.
        magma: Answers about who asks.
        store: The games' stores.
        deals: The stores' hot deals.
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
        return xml(plain)

    # The game adds what it bought itself once the answer is a success, or for a box the one
    # product the answer lists (BFHeroes.exe 1.89 0x007bafb7, 0x006a0335).
    @router.post("/ofb/purchase/{userId}/{productId}")
    async def purchase(
        persona_id: UserId, product_id: ProductId, session_id: SessionId = None
    ) -> Response:
        persona = await magma.persona(session_id, game, persona_id, own=True)
        won = await purchases.purchase(game, persona, product_id)
        return xml(products([_entry(won)]) if won else success())

    # The game reads the hero's new balance and the items it no longer holds (BFHeroes.exe
    # 1.89 0x006a17d0).
    @router.post("/nucleus/refundAbilities/{userId}")
    async def refund(persona_id: UserId, session_id: SessionId = None) -> Response:
        persona = await magma.persona(session_id, game, persona_id, own=True)
        done = await purchases.refund(game, persona)
        answer = refunded(done.currency, done.balance, done.items) if done else nothing_refunded()
        return xml(answer)

    # A player's game and the dedicated server keeping the player change the items it uses.
    @router.post("/nucleus/entitlement/{entitlementId}/useCount/{uses}")
    async def use(
        entitlement_id: EntitlementId,
        uses: int,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        await _holder(magma, game, writes, entitlement_id, session_id, relay_token)
        await writes.use_up(entitlement_id, uses)
        return xml(success())

    @router.post("/nucleus/entitlement/{entitlementId}/status/{status}")
    async def take_out(
        entitlement_id: EntitlementId,
        status: str,
        session_id: SessionId = None,
        relay_token: RelayToken = None,
    ) -> Response:
        await _holder(magma, game, writes, entitlement_id, session_id, relay_token)
        await writes.take_out(entitlement_id, status)
        return xml(success())

    # The game asks in the language of its menus, such as en.
    @router.get("/{language}/game/hotDeals")
    async def hot(language: str) -> Response:
        listed = await deals.hot_deals(game, game.language_of(language))
        entries = [_deal(game, deal) for deal in listed]
        return xml(hot_deals(entries))

    return router


async def _holder(
    magma: Magma,
    game: Game,
    writes: ItemWrites,
    entitlement_id: int,
    session_id: str | None,
    relay_token: str | None,
) -> None:
    # Who asks first, so that only an asker who may change items learns which exist.
    magma.session(session_id)
    holder = await writes.holder(entitlement_id)
    await magma.persona(session_id, game, holder, relay_token=relay_token)


def _deal(game: Game, deal: Deal) -> BundleEntry:
    listed = deal.listed
    bundle = listed.bundle
    lead = listed.members[0] if listed.members else None
    return BundleEntry(
        id=deal.product_id,
        name=deal.name,
        description=deal.description,
        tag=lead.ownership.tag(lead.item_id) if lead else "",
        game_order=deal.order,
        published=bundle.starts or listed.created,
        created=listed.created,
        random=bundle.box is not None,
        currency=bundle.currency,
        price=bundle.price,
        original_price=bundle.original_price,
        members=tuple(_entry(member) for member in deal.members),
        small_image=deal_art_url(game, listed.id, big=False),
        big_image=deal_art_url(game, listed.id, big=True),
    )


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
