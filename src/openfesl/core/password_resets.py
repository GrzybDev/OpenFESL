from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from openfesl.core.accounts import STATE_ERRORS, Account, AccountState, AccountStore
from openfesl.core.limits import Attempts, Mailings
from openfesl.core.logins import Logins
from openfesl.core.mail import Outbox
from openfesl.core.problems import Problem, password_problem
from openfesl.core.verification import Link, digest, new_link

# A reset link sets the account's game password, so it works for a short time only.
VALID_FOR = timedelta(hours=1)
PATH = "/password-reset/{token}"


@dataclass(frozen=True, slots=True)
class KeptReset:
    """A reset link as the store hands it back.

    Attributes:
        account_id: The account whose game password it sets.
        expired: Whether it stopped working.
    """

    account_id: int
    expired: bool


@dataclass(frozen=True, slots=True)
class PasswordSet:
    """A new game password set through a reset link.

    Attributes:
        account: The account.
        unlinked: Whether identities at sign-in services were unlinked, as they were linked while
            the address waited for the link that the reset's own link stood in for.
    """

    account: Account
    unlinked: bool


@dataclass(frozen=True, slots=True)
class ResetGone:
    """A reset link that does not work.

    Attributes:
        expired: Whether it expired; otherwise it was used already or never existed.
    """

    expired: bool


class ResetStore(Protocol):
    """Where the links that set accounts' new game passwords are kept, by their tokens' digests."""

    async def keep_reset(self, account_id: int, link: Link) -> None:
        """Keep a reset link, which replaces the account's earlier one.

        Args:
            account_id: The account whose game password it sets.
            link: The link as the store keeps it.
        """

    async def reset_of(self, token_hash: bytes) -> KeptReset | None:
        """Return the reset link that a token names, without using it; None for an unknown one.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """

    async def use_reset(self, token_hash: bytes, password_hash: str) -> int | None:
        """Use a working reset link: set its account's game password and confirm its address.

        The account's web sessions and remembered logins end, as with any new password. An
        address that waited for its link loses the identities at sign-in services linked
        meanwhile, which its mailbox never proved.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
            password_hash: The new game password's hash.

        Returns:
            How many identities it unlinked; None when the link did not work, once used or
            expired.
        """


class PasswordResets:
    """Sets a new game password through a link sent to the account's address.

    Opening the link proves the mailbox, so it also confirms an address that waits for its link.
    """

    def __init__(
        self,
        store: ResetStore,
        accounts: AccountStore,
        logins: Logins,
        outbox: Outbox,
        mailings: Mailings,
        attempts: Attempts,
    ) -> None:
        """Reset game passwords through the server's mail.

        Args:
            store: Where the links are kept.
            accounts: Where the accounts are kept.
            logins: Hashes game passwords.
            outbox: Sends the mail.
            mailings: Limits the mail to each address.
            attempts: Counts the failed sign-ins with each address, whose pause a new game
                password ends.
        """
        self._store = store
        self._accounts = accounts
        self._logins = logins
        self._outbox = outbox
        self._mailings = mailings
        self._attempts = attempts

    async def request(self, address: str, language: str) -> None:
        """Send an address the link that sets its account's new game password.

        Every address gets the same answer: only the address of an account that may sign in gets
        the mail, at most once per interval, counted whatever the address belongs to.

        Args:
            address: The e-mail address the account signs in with.
            language: The language code of the page it was asked from.
        """
        if self._mailings.reserve(address):
            return
        found = await self._accounts.find(address)
        deactivated = STATE_ERRORS[AccountState.DEACTIVATED]
        if found is None or found.account.server or found.account.login_error() == deactivated:
            return
        token, link = new_link(VALID_FOR)
        await self._store.keep_reset(found.account.id, link)
        links = {"link": PATH.format(token=token)}
        times = {"expires": link.expires_at}
        self._outbox.send(found.account.nuid, "account.mail.reset", language, links, times)

    async def opened(self, token: str) -> Account | ResetGone:
        """Return the account whose game password a reset link sets, if the link works.

        Args:
            token: The token the link carries.
        """
        kept = await self._store.reset_of(digest(token))
        account = await self._accounts.account(kept.account_id) if kept else None
        if kept is None or account is None or kept.expired:
            return ResetGone(expired=bool(kept and kept.expired))
        return account

    async def reset(self, token: str, password: str) -> PasswordSet | Problem | ResetGone:
        """Set a new game password through a reset link, which works once.

        Args:
            token: The token the link carries.
            password: The new game password.

        Returns:
            The new game password, what is wrong with it, with the link still working, or the
            link that does not work.
        """
        opened = await self.opened(token)
        if isinstance(opened, ResetGone):
            return opened
        if problem := password_problem(password):
            return problem
        unlinked = await self._store.use_reset(digest(token), await self._logins.hash(password))
        if unlinked is None:
            return ResetGone(expired=False)
        self._attempts.clear(opened.nuid)
        return PasswordSet(opened, unlinked > 0)
