from fastapi import APIRouter
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.magma import Magma
from openfesl.protocol.magma import name, success
from openfesl.web.magma import RelayToken, UserId, xml
from openfesl.web.magma_heroes import PersonaId


def servers_router(game: Game, magma: Magma) -> APIRouter:
    """Return the Magma calls that a game's dedicated servers send without a session.

    The game sends them with no cookie, so only the relay token tells which server asks.

    Args:
        game: The game whose Magma host they reach.
        magma: Answers about who asks.
    """
    router = APIRouter()

    @router.get("/nucleus/name/{userId}")
    async def player_name(account_id: UserId, relay_token: RelayToken = None) -> Response:
        return xml(name(await magma.name(relay_token, game, account_id)))

    @router.post("/nucleus/wallets/{userId}/{currency}/{amount}/{transaction}")
    async def credit(
        persona_id: UserId,
        currency: str,
        amount: int,
        transaction: str,
        relay_token: RelayToken = None,
    ) -> Response:
        await magma.credit(relay_token, game, persona_id, currency, amount, transaction)
        return xml(success())

    @router.post("/nucleus/stackEntitlementUseCount/{userId}/{personaId}/{tag}/{count}")
    async def stack_chests(
        account_id: UserId,
        persona_id: PersonaId,
        tag: str,
        count: int,
        relay_token: RelayToken = None,
    ) -> Response:
        await magma.stack_chests(relay_token, game, account_id, persona_id, tag, count)
        return xml(success())

    return router
