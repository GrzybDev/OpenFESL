from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import Response

from openfesl.core.games import Game
from openfesl.core.magma import Magma
from openfesl.protocol.magma import name, success
from openfesl.web.magma import XML, RelayToken, UserId
from openfesl.web.magma_heroes import PersonaId

# The original service names an account in these paths userId too, also in its refusals.
AccountId = Annotated[int, Path(alias="userId")]


def servers_router(game: Game, magma: Magma) -> APIRouter:
    """Return the Magma calls that a game's dedicated servers send without a session.

    The game sends them with no cookie, so only the relay token tells which server asks.

    Args:
        game: The game whose Magma host they reach.
        magma: Answers about who asks.
    """
    router = APIRouter()

    @router.get("/nucleus/name/{userId}")
    async def player_name(account_id: AccountId, relay_token: RelayToken = None) -> Response:
        return Response(name(await magma.name(relay_token, game, account_id)), media_type=XML)

    @router.post("/nucleus/wallets/{userId}/{currency}/{amount}/{transaction}")
    async def credit(
        persona_id: UserId,
        currency: str,
        amount: int,
        transaction: str,
        relay_token: RelayToken = None,
    ) -> Response:
        await magma.credit(relay_token, game, persona_id, currency, amount, transaction)
        return Response(success(), media_type=XML)

    @router.post("/nucleus/stackEntitlementUseCount/{userId}/{personaId}/{tag}/{count}")
    async def stack_chests(
        account_id: AccountId,
        persona_id: PersonaId,
        tag: str,
        count: int,
        relay_token: RelayToken = None,
    ) -> Response:
        await magma.stack_chests(relay_token, game, account_id, persona_id, tag, count)
        return Response(success(), media_type=XML)

    return router
