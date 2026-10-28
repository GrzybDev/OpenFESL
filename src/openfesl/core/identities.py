from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Identity:
    """An account at a sign-in service, as the service answered a sign-in.

    Attributes:
        service: The service's id, such as discord.
        subject: The account's id at the service, which never changes.
        name: The account's name there.
        address: The e-mail address the service verified for it; None when it verified none.
    """

    service: str
    subject: str
    name: str
    address: str | None


@dataclass(frozen=True, slots=True)
class LinkedIdentity:
    """An identity at a sign-in service that signs an account in.

    Attributes:
        service: The service's id.
        subject: The account's id at the service.
        name: The account's name there when it was linked.
        linked: When it was linked.
    """

    service: str
    subject: str
    name: str
    linked: datetime


class IdentityStore(Protocol):
    """Where the identities linked to accounts are kept; only the service, id and name."""

    async def account_of(self, service: str, subject: str) -> int | None:
        """Return the account that an identity signs in, if it is linked.

        Args:
            service: The service's id.
            subject: The account's id at the service.
        """

    async def linked(self, account_id: int) -> list[LinkedIdentity]:
        """Return the identities linked to an account, the oldest first.

        Args:
            account_id: The account.
        """

    async def link(self, account_id: int, identity: Identity) -> bool:
        """Link an identity to an account, which it signs in from then on.

        Args:
            account_id: The account.
            identity: The identity.

        Returns:
            Whether it was linked, which it is not when it opens another account or the account
            has an identity of that service already.
        """

    async def unlink(self, account_id: int, service: str, services: Collection[str]) -> bool:
        """Unlink an account's identity at a service, which no longer signs it in.

        Args:
            account_id: The account.
            service: The service's id.
            services: The ids of the configured sign-in services, whose identities alone sign in.

        Returns:
            Whether it was unlinked, which it is not when the account has none there or when it
            is the account's last way in, with no game password and no other identity at a
            configured service.
        """
