import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

from openfesl.core.fesl import PARAMETERS_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Grant, ProductKey
from openfesl.protocol.acct import (
    EntitlementInfo,
    NuEntitleUser,
    NuEntitleUserAnswer,
    NuGetEntitlementCountAnswer,
    NuGetEntitlements,
    NuGetEntitlementsAnswer,
    NuGrantEntitlement,
    Product,
)
from openfesl.protocol.message import Message

# For annotations only, since hosting reaches the account service, which imports this module.
if TYPE_CHECKING:
    from openfesl.core.hosting import Hosting

logger = logging.getLogger(__name__)

NOT_LOGGED_IN = 20
UNKNOWN_KEY = 181


@dataclass(frozen=True, slots=True)
class Entitlement:
    """Something an account owns, such as a game or downloadable content.

    Attributes:
        id: The entitlement's number.
        tag: What it entitles to, such as BFBC2:PC:VIETNAM_ACCESS.
        group: The group the game asks for by name, if any.
        product: The store product it came with, if any.
        status: ACTIVE, or the admin's reason for taking it away.
        granted: When it was granted.
    """

    id: int
    tag: str
    group: str | None
    product: str | None
    status: str
    granted: date


@dataclass(frozen=True, slots=True)
class EntitlementQuery:
    """Filters for an account's entitlements; an empty one does not filter."""

    id: str = ""
    tag: str = ""
    product: str = ""
    group: str = ""
    status: str = ""


class EntitlementStore(Protocol):
    """Where entitlements and the product keys that grant them are kept."""

    async def entitled(self, account_id: int, tag: str) -> bool:
        """Tell whether an account holds an active entitlement.

        Args:
            account_id: The account.
            tag: The entitlement's tag.
        """

    async def grants(self, code: str) -> list[Grant] | None:
        """Return what an enabled product key grants, without granting it.

        Args:
            code: The key, matched regardless of case.

        Returns:
            What the key grants, or None when no enabled key has that code.
        """

    async def redeem(self, account_id: int, code: str) -> list[Grant] | None:
        """Grant an account what an enabled product key grants.

        Entitlements the account holds already, revoked ones included, stay as they are.

        Args:
            account_id: The account.
            code: The key, matched regardless of case.

        Returns:
            What the key grants, or None when no enabled key has that code.
        """

    async def of_account(self, account_id: int, query: EntitlementQuery) -> list[Entitlement]:
        """Return an account's entitlements, filtered by whichever filters are not empty.

        Args:
            account_id: The account.
            query: The filters.
        """

    async def has_keys(self) -> bool:
        """Tell whether any product key exists, enabled or not."""

    async def import_keys(self, keys: Sequence[ProductKey]) -> int:
        """Add the keys whose codes do not exist yet, leaving existing keys untouched.

        Args:
            keys: Keys from the games' data files.

        Returns:
            How many keys were added.
        """


class EntitlementService:
    """The acct transactions that redeem product keys and list entitlements."""

    def __init__(self, store: EntitlementStore, hosting: Hosting) -> None:
        """Serve entitlements from a store.

        Args:
            store: Where entitlements and product keys are kept.
            hosting: Where the games are that dedicated servers host.
        """
        self._store = store
        self._hosting = hosting

    def routes(self) -> dict[tuple[str, str], Route[Any]]:
        """Return the handlers of the service's transactions."""
        return {
            ("acct", "NuEntitleUser"): Route(NuEntitleUser, self.entitle_user),
            ("acct", "NuGetEntitlements"): Route(NuGetEntitlements, self.entitlements),
            ("acct", "NuGetEntitlementCount"): Route(NuGetEntitlements, self.count),
            ("acct", "NuGrantEntitlement"): Route(NuGrantEntitlement, self.grant),
        }

    async def entitle_user(
        self, connection: FeslConnection, request: NuEntitleUser
    ) -> NuEntitleUserAnswer:
        """Redeem a product key for the logged-in account.

        Args:
            connection: The FESL connection the request came on.
            request: The key.

        Raises:
            TransactionError: When nobody is logged in or no enabled key has that code.
        """
        account = connection.session.account
        if account is None:
            raise TransactionError(NOT_LOGGED_IN)
        if account.server:
            raise TransactionError(PARAMETERS_ERROR)
        grants = await self._store.redeem(account.id, request.key)
        if grants is None:
            raise TransactionError(UNKNOWN_KEY)
        logger.info("Account %d redeemed a product key", account.id)
        # The game takes an answer without entries for a failed redeem, and names what the key
        # unlocked after the licence whose tag an entry's name matches, so each product gets an
        # entry named after its first tag, and the grants without a product share one.
        products: dict[str | None, str] = {}
        for grant in grants:
            products.setdefault(grant.product, grant.tag)
        return NuEntitleUserAnswer(
            product_list=[
                Product(product_id=product, name=tag) for product, tag in products.items()
            ]
        )

    async def grant(self, connection: FeslConnection, request: NuGrantEntitlement) -> Message:
        """Confirm an entitlement that the logged-in account holds already.

        Nothing new is granted: the SDK names no account, so any game could grant itself
        anything, while what an account owns comes from product keys and the admin.

        Args:
            connection: The FESL connection the request came on.
            request: The entitlement.

        Raises:
            TransactionError: When nobody is logged in, a dedicated server asks or the account
                does not hold the entitlement.
        """
        account = connection.session.account
        if account is None:
            raise TransactionError(NOT_LOGGED_IN)
        if account.server or not await self._store.entitled(account.id, request.entitlement_tag):
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
                    grant_date=entitlement.granted,
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

    async def _find(
        self, connection: FeslConnection, request: NuGetEntitlements
    ) -> list[Entitlement]:
        account = connection.session.account
        if account is None:
            raise TransactionError(NOT_LOGGED_IN)
        # The game takes whatever a query by its network card's address finds as a licence of
        # that PC, and the service binds no entitlement to a device.
        if request.device_physical_id:
            return []
        owner = request.master_user_id or account.id
        # Anyone logs in like a dedicated server, so only one that hosts a game players see
        # reads its players' entitlements, such as their veteran tags.
        if owner != account.id and not (
            account.server and await self._hosting.admits(connection.session)
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
