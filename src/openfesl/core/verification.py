import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol

from openfesl.core.admins import Admin
from openfesl.core.fesl import TransactionError
from openfesl.core.mail import Outbox

# For annotations only, since the account service uses this module.
if TYPE_CHECKING:
    from openfesl.core.accounts import Account

logger = logging.getLogger(__name__)

PENDING_AUTHORIZATION = 105
# A login after that sends a new link, so this also bounds how often an address that someone
# else registered gets mail.
VALID_FOR = timedelta(days=1)
PATH = "/verify/{token}"


@dataclass(frozen=True, slots=True)
class Link:
    """A link that confirms an account's e-mail address, as the store keeps it.

    Attributes:
        token_hash: The SHA-256 digest of the token the link carries.
        expires_at: When the link stops working.
    """

    token_hash: bytes
    expires_at: datetime


class Confirmation(Enum):
    """Why a link confirms nothing."""

    EXPIRED = auto()
    UNKNOWN = auto()
    """No account waits for the link, as when it was used already."""


class VerificationStore(Protocol):
    """Where the links of accounts waiting for their e-mail addresses to be confirmed are kept."""

    async def renew(self, account_id: int, link: Link) -> bool:
        """Replace the link of an account once its link expired.

        Args:
            account_id: The account.
            link: The new link.

        Returns:
            Whether the link was replaced, which it is not while the last one works or once
            the address is confirmed.
        """

    async def replace(self, account_id: int, link: Link) -> bool:
        """Replace the link of an account that waits for its address to be confirmed.

        Args:
            account_id: The account.
            link: The new link.

        Returns:
            Whether the link was replaced, which it is not once the address is confirmed.
        """

    async def waiting(self, token_hash: bytes) -> int | Confirmation:
        """Return the account that waits for a link, without confirming its address.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.

        Returns:
            The account's number, or why the link confirms nothing.
        """

    async def confirm(self, token_hash: bytes) -> int | Confirmation:
        """Confirm the address of the account that waits for a link.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.

        Returns:
            The account's number, or why nothing was confirmed.
        """

    async def confirm_account(self, account_id: int, admin: Admin | None) -> None:
        """Confirm an account's address by hand and record it among the admin's actions.

        Args:
            account_id: The account.
            admin: The admin who confirmed it; None for the command line.
        """


def new_link(valid_for: timedelta | None = None) -> tuple[str, Link]:
    """Return a fresh token and the link it makes, as the store keeps it.

    Args:
        valid_for: How long the link works; a confirmation link's time when None.
    """
    token = secrets.token_urlsafe(32)
    lasts = VALID_FOR if valid_for is None else valid_for
    return token, Link(digest(token), datetime.now(UTC) + lasts)


def digest(token: str) -> bytes:
    """Return the digest that the store keeps of a link's token, which it never keeps itself.

    Args:
        token: The token.
    """
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).digest()


class EmailVerification:
    """Confirms the e-mail addresses of new accounts with links sent to them."""

    def __init__(self, store: VerificationStore, outbox: Outbox) -> None:
        """Send links that lead to the server's verification page.

        Args:
            store: Where the links are kept.
            outbox: Sends the mail.
        """
        self._store = store
        self._outbox = outbox

    def send(
        self, to: str, token: str, link: Link, language: str, texts: str = "verification.mail"
    ) -> None:
        """Start sending an account's address the link that confirms it.

        The game's answer does not wait for the mail: the game gives up on NuAddAccount and
        NuLogin after 20 s, which a slow mail server can take. A link that does not reach the
        mail server is only logged: the account keeps waiting, the next login after its expiry
        sends a new one, and the admin can confirm by hand.

        Args:
            to: The account's address, its login name.
            token: The token the link carries.
            link: The link as the store keeps it.
            language: The language code of the game it registers or logs in from; English
                where the catalogues hold no texts in it.
            texts: The catalogues' key of the mail, which tells whether the account waits for
                the link.
        """
        path = PATH.format(token=token)
        self._outbox.send(to, texts, language, {"link": path}, {"expires": link.expires_at})

    async def require(self, account: Account, language: str) -> None:
        """Refuse the login of an account whose address waits to be confirmed.

        Once the last link expired, a new one goes out.

        Args:
            account: The account that logs in.
            language: The language code of the game it logs in from.

        Raises:
            TransactionError: When the account waits for its address to be confirmed.
        """
        if account.verification_expires_at is None:
            return
        if account.verification_expires_at <= datetime.now(UTC):
            token, link = new_link()
            if await self._store.renew(account.id, link):
                self.send(account.nuid, token, link, language)
        raise TransactionError(PENDING_AUTHORIZATION)

    async def resend(self, account: Account, language: str) -> datetime | None:
        """Send an account that waits for its address to be confirmed a link that replaces its last.

        Args:
            account: The account.
            language: The language code of the page it was asked from.

        Returns:
            When the new link expires; None once the address is confirmed and nothing went out.
        """
        token, link = new_link()
        if not await self._store.replace(account.id, link):
            return None
        self.send(account.nuid, token, link, language)
        return link.expires_at

    async def find(self, token: str) -> int | Confirmation:
        """Return the account whose address a link confirms, without confirming it.

        Args:
            token: The token the link carries.

        Returns:
            The account's number, or why the link confirms nothing.
        """
        return await self._store.waiting(digest(token))

    async def confirm(self, token: str) -> int | Confirmation:
        """Confirm the address of the account a link was sent to.

        Args:
            token: The token the link carries.

        Returns:
            The account's number, or why nothing was confirmed.
        """
        confirmed = await self._store.confirm(digest(token))
        outcome = "confirmed" if isinstance(confirmed, int) else confirmed.name.lower()
        logger.info("A verification link was used: %s", outcome)
        return confirmed
