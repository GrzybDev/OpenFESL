import asyncio
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol

from babel.dates import format_datetime

from openfesl.core.fesl import TransactionError
from openfesl.core.texts import BASE_LANGUAGE, languages, localize, translate

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
    """What opening a link did."""

    CONFIRMED = auto()
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

    async def confirm(self, token_hash: bytes) -> Confirmation:
        """Confirm the address of the account that waits for a link.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """

    async def confirm_account(self, account_id: int, admin: str | None) -> None:
        """Confirm an account's address by hand and record it among the admin's actions.

        Args:
            account_id: The account.
            admin: The admin who confirmed it; None for the command line.
        """


class Mailer(Protocol):
    """Sends e-mail."""

    async def send(self, to: str, subject: str, body: str, language: str) -> None:
        """Hand a plain-text e-mail to the mail server.

        Args:
            to: The recipient's address.
            subject: The subject.
            body: The text.
            language: The language code of the text.

        Raises:
            OSError: When the mail server cannot be reached or refuses the e-mail.
        """


def new_link() -> tuple[str, Link]:
    """Return a fresh token and the link it makes, as the store keeps it."""
    token = secrets.token_urlsafe(32)
    return token, Link(_digest(token), datetime.now(UTC) + VALID_FOR)


class EmailVerification:
    """Confirms the e-mail addresses of new accounts with links sent to them."""

    def __init__(
        self, store: VerificationStore, mailer: Mailer | None, public_url: str | None
    ) -> None:
        """Send links that lead to the server's verification page.

        Args:
            store: Where the links are kept.
            mailer: Sends the e-mail; None when the deployment set no mail server.
            public_url: The address the server's pages are reached at, such as
                https://fesl.example.com; None when the deployment set none.
        """
        self._store = store
        self._mailer = mailer
        self._public_url = public_url
        self._deliveries: set[asyncio.Task[None]] = set()

    def send(self, to: str, token: str, link: Link, language: str) -> None:
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
        """
        if self._mailer is None or self._public_url is None:
            logger.error("Cannot send a verification link: no mail server or public URL is set")
            return
        # The date and the mail's language follow the texts, which would otherwise be English
        # around a date in the game's language, and Babel refuses locales it does not know.
        if language not in languages():
            language = BASE_LANGUAGE
        # The link opens the page in the language of the mail.
        url = self._public_url.rstrip("/") + localize(PATH.format(token=token), language)
        expires = format_datetime(link.expires_at, "long", tzinfo=UTC, locale=language)
        subject = translate("verification.mail.subject", language)
        body = translate("verification.mail.body", language).format(link=url, expires=expires)
        delivery = asyncio.create_task(_deliver(self._mailer, to, subject, body, language))
        # The loop keeps only weak references to its tasks.
        self._deliveries.add(delivery)
        delivery.add_done_callback(self._deliveries.discard)

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

    async def confirm(self, token: str) -> Confirmation:
        """Confirm the address of the account a link was sent to.

        Args:
            token: The token the link carries.
        """
        confirmation = await self._store.confirm(_digest(token))
        logger.info("A verification link was opened: %s", confirmation.name.lower())
        return confirmation


async def _deliver(mailer: Mailer, to: str, subject: str, body: str, language: str) -> None:
    try:
        await mailer.send(to, subject, body, language)
    except OSError:
        logger.exception("Could not send a verification link")


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8", "surrogateescape")).digest()
