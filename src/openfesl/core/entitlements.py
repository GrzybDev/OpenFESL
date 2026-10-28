import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Game, Grant
from openfesl.protocol.acct import (
    EntitlementInfo,
    NuGetEntitlementCountAnswer,
    NuGetEntitlements,
    NuGetEntitlementsAnswer,
    NuGrantEntitlement,
)
from openfesl.protocol.message import Message

# For annotations only, since the trust checks reach the account service, which imports this
# module.
if TYPE_CHECKING:
    from openfesl.core.trust import ServerTrust

logger = logging.getLogger(__name__)

NOT_LOGGED_IN = 20


@dataclass(frozen=True, slots=True)
class Entitlement:
    """Something an account owns, such as a game or downloadable content, or a persona's item.

    Attributes:
        id: The entitlement's number.
        tag: What it entitles to, such as BFBC2:PC:VIETNAM_ACCESS, or a persona's item and how
            the persona owns it, such as 3002-unlm.
        group: The group the game asks for by name, if any.
        product: The store product it came with, if any.
        status: ACTIVE, or the admin's reason for taking it away.
        granted: When it was granted.
        uses: The uses or seconds left of an item limited by them.
        expires: When an item limited by date ends.
    """

    id: int
    tag: str
    group: str | None
    product: str | None
    status: str
    granted: datetime
    uses: int | None
    expires: datetime | None


@dataclass(frozen=True, slots=True)
class StatusChange:
    """When the admin last revoked an entitlement or granted it again, and who did.

    Attributes:
        at: When.
        by: The admin's display name; None for the command line.
    """

    at: datetime
    by: str | None


@dataclass(frozen=True, slots=True)
class EntitlementQuery:
    """Filters for an account's entitlements; an empty one does not filter."""

    id: str = ""
    tag: str = ""
    product: str = ""
    group: str = ""
    status: str = ""


class EntitlementStore(Protocol):
    """Where the accounts' entitlements are kept."""

    async def entitled(self, account_id: int, tag: str, product: str | None = None) -> bool:
        """Tell whether an account holds an active entitlement.

        Args:
            account_id: The account.
            tag: The entitlement's tag.
            product: The product the entitlement must belong to, regardless of case; any when
                omitted.
        """

    async def grant(self, account_id: int, grant: Grant) -> bool:
        """Grant an account an entitlement, which stays as it is when the account holds it.

        Args:
            account_id: The account.
            grant: The entitlement.

        Returns:
            Whether the account holds it active, which it does not once the admin revoked it.
        """

    async def of_account(self, account_id: int, query: EntitlementQuery) -> list[Entitlement]:
        """Return an account's entitlements, filtered by whichever filters are not empty.

        Args:
            account_id: The account.
            query: The filters.
        """

    async def of_persona(self, persona_id: int, tag: str = "") -> list[Entitlement]:
        """Return the items a persona owns, one entitlement per item.

        Args:
            persona_id: The persona.
            tag: A tag whose item alone is returned, however the tag says it is owned; every
                item when empty.
        """


class EntitlementService:
    """The acct transactions that list entitlements and let dedicated servers grant them."""

    def __init__(self, store: EntitlementStore, trust: ServerTrust) -> None:
        """Serve entitlements from a store.

        Args:
            store: Where the entitlements are kept.
            trust: Which dedicated servers host games that players see, and who plays in them.
        """
        self._store = store
        self._trust = trust

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("acct", "NuGetEntitlements"): Route(NuGetEntitlements, self.entitlements),
            ("acct", "NuGetEntitlementCount"): Route(NuGetEntitlements, self.count),
            ("acct", "NuGrantEntitlement"): Route(NuGrantEntitlement, self.grant),
        }

    async def grant(self, connection: FeslConnection, request: NuGrantEntitlement) -> Message:
        """Grant a player what its dedicated server may grant, or confirm what a player holds.

        A player's game grants nothing new: its SDK names no account, so any game could grant
        itself anything, while what an account owns comes from product keys and the admin. A
        dedicated server that hosts a game players see grants the players in it the
        entitlements the game's data lists as automatic.

        Args:
            connection: The FESL connection the request came on.
            request: The entitlement, and the player's account when a server grants it.

        Raises:
            TransactionError: When nobody is logged in, a server may not grant the entitlement
                to the account, or the account does not hold it, as after the admin revoked it.
        """
        account = connection.session.account
        if account is None or connection.game is None:
            raise TransactionError(NOT_LOGGED_IN)
        if account.server:
            held = await self._grant_automatic(connection, connection.game, request)
        else:
            held = await self._store.entitled(account.id, request.entitlement_tag)
        if not held:
            raise TransactionError(PARAMETERS_ERROR)
        return Message()

    async def entitlements(
        self, connection: FeslConnection, request: NuGetEntitlements
    ) -> NuGetEntitlementsAnswer:
        """Return the entitlements of the session's account, or of a player a server asks about.

        Args:
            connection: The FESL connection the request came on.
            request: The filters.
        """
        found = await self._find(connection, request)
        return NuGetEntitlementsAnswer(
            entitlements=[
                EntitlementInfo(
                    entitlement_id=str(entitlement.id),
                    entitlement_tag=entitlement.tag,
                    product_id=entitlement.product,
                    status=entitlement.status,
                    grant_date=entitlement.granted.date(),
                )
                for entitlement in found
            ]
        )

    async def count(
        self, connection: FeslConnection, request: NuGetEntitlements
    ) -> NuGetEntitlementCountAnswer:
        """Return how many entitlements match the filters.

        Args:
            connection: The FESL connection the request came on.
            request: The filters.
        """
        return NuGetEntitlementCountAnswer(
            entitlement_count=len(await self._find(connection, request))
        )

    async def _grant_automatic(
        self, connection: FeslConnection, game: Game, request: NuGrantEntitlement
    ) -> bool:
        tag = request.entitlement_tag
        grant = next((grant for grant in game.automatic_grants if grant.tag == tag), None)
        if grant is None or not await self._trust.admits_player(
            connection.session, request.master_id
        ):
            return False
        logger.info("A dedicated server grants account %d %s", request.master_id, tag)
        return await self._store.grant(request.master_id, grant)

    async def _find(
        self, connection: FeslConnection, request: NuGetEntitlements
    ) -> list[Entitlement]:
        account = connection.session.account
        if account is None:
            raise TransactionError(NOT_LOGGED_IN)
        # The game's SDK can ask for licences bound to a PC's network card, which the service
        # has none of.
        if request.device_physical_id:
            return []
        owner = request.master_user_id or account.id
        # Anyone logs in like a dedicated server, so only one that hosts a game players see
        # reads the entitlements of the players in it, such as their veteran tags.
        if owner != account.id and not (
            account.server and await self._trust.admits_player(connection.session, owner)
        ):
            raise TransactionError(PARAMETERS_ERROR)
        query = EntitlementQuery(
            id=request.entitlement_id,
            tag=request.entitlement_tag,
            product=request.product_id,
            group=request.group_name,
            status=request.status,
        )
        return await self._store.of_account(owner, query)
