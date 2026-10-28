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
