from openfesl.core.entitlements import EntitlementStore
from openfesl.core.inventory import DELETED, DISABLED
from openfesl.core.magma import VALIDATION_FAILED, MagmaError

# A game's change of an item that is no hero's item in use (BFHeroes.exe 1.89 0x0069def5 reads
# this code as error 6008), and the causes of changes it may not make.
NO_SUCH_ENTITLEMENT = "NO_SUCH_ENTITLEMENT"
UNKNOWN_ENTITLEMENT = "UNKNOWN_ENTITLEMENT"
NEGATIVE_USES = "NEGATIVE_USES"
STATUS_NOT_ALLOWED = "STATUS_NOT_ALLOWED"


class ItemWrites:
    """The changes a game makes to the items its heroes use: uses used, items out of use."""

    def __init__(self, entitlements: EntitlementStore) -> None:
        """Change the items where they are kept.

        Args:
            entitlements: Where the heroes' items are kept.
        """
        self._entitlements = entitlements

    async def holder(self, entitlement_id: int) -> int:
        """Return the persona whose item in use an entitlement is, as a game names one it changes.

        Args:
            entitlement_id: The entitlement.

        Raises:
            MagmaError: When it is no persona's item in use, such as an account's own or one
                the admin took away.
        """
        persona_id = await self._entitlements.holder(entitlement_id)
        if persona_id is None:
            raise MagmaError(NO_SUCH_ENTITLEMENT, UNKNOWN_ENTITLEMENT)
        return persona_id

    async def use_up(self, entitlement_id: int, uses: int) -> None:
        """Take the uses a hero used from its item, which never gains any this way.

        The game names the uses it registers as consumed (BFHeroes.exe 1.89 strings
        "registered consumption of %d units of item %d - entitlement:%s" and
        ConsumeItemTransaction<%d %s %d>), so a misreading could only cost a hero uses.

        Args:
            entitlement_id: The item's entitlement, which is in use.
            uses: How many uses were used.

        Raises:
            MagmaError: When the uses are fewer than none.
        """
        if uses < 0:
            raise MagmaError(VALIDATION_FAILED, NEGATIVE_USES)
        await self._entitlements.use_up(entitlement_id, uses)

    async def take_out(self, entitlement_id: int, status: str) -> None:
        """Take a hero's item out of use or out of its list, as its game writes the item's status.

        Args:
            entitlement_id: The item's entitlement, which is in use.
            status: DISABLED, which ends a rental or an item limited by uses and keeps any
                other listed out of use, or DELETED, which removes it.

        Raises:
            MagmaError: When the status puts nothing out of use, as ACTIVE, or is the admin's
                decision, as BANNED.
        """
        if status not in (DISABLED, DELETED):
            raise MagmaError(VALIDATION_FAILED, STATUS_NOT_ALLOWED)
        await self._entitlements.take_out(entitlement_id, status)
