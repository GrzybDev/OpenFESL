from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.identities import Identity
from openfesl.core.limits import Mailings, Wait
from openfesl.core.mail import Outbox
from openfesl.core.sign_in import LinkSent, WebSignIn
from openfesl.core.verification import Link, digest, new_link

# A proof link signs in, so it works for a shorter time than a confirmation link.
VALID_FOR = timedelta(hours=1)
PATH = "/sign-in/proof/{token}"


@dataclass(frozen=True, slots=True)
class KeptProof:
    """A proof link as the store hands it back.

    Attributes:
        account_id: The account it proves.
        identity: The identity it links to the account.
        expired: Whether it stopped working.
    """

    account_id: int
    identity: Identity
    expired: bool


@dataclass(frozen=True, slots=True)
class ProofFound:
    """A working proof link, which links its identity to its account once it is used.

    Attributes:
        account: The account it proves.
        identity: The identity it links.
    """

    account: Account
    identity: Identity


@dataclass(frozen=True, slots=True)
class ProofOpened:
    """A proof link that linked its identity to its account.

    Attributes:
        account: The account, which may sign in.
        identity: The identity.
        linked: Whether the identity was linked, which it is not when it was linked elsewhere
            meanwhile.
    """

    account: Account
    identity: Identity
    linked: bool


@dataclass(frozen=True, slots=True)
class ProofGone:
    """A proof link that no longer works.

    Attributes:
        service: The id of the service it was for, when it expired; None for a link that was
            used already or never existed.
    """

    service: str | None


class ProofStore(Protocol):
    """Where the proof links e-mailed to accounts' addresses are kept, by their tokens' digests."""

    async def keep_proof(self, account_id: int, identity: Identity, link: Link) -> None:
        """Keep a proof link, which replaces the account's earlier ones for the same service.

        Args:
            account_id: The account it proves.
            identity: The identity it links to the account.
            link: The link as the store keeps it.
        """

    async def proof_of(self, token_hash: bytes) -> KeptProof | None:
        """Return the proof link that a token names, without using it; None for an unknown one.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """

    async def take_proof(self, token_hash: bytes) -> KeptProof | None:
        """Take a proof link out of use and return it; None for one the store does not keep.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """


class ProofMail:
    """Proves an account with a link sent to its address, which links a new identity to it."""

    def __init__(
        self,
        store: ProofStore,
        accounts: AccountStore,
        sign_in: WebSignIn,
        outbox: Outbox,
        mailings: Mailings,
    ) -> None:
        """Send proof links through the server's mail.

        Args:
            store: Where the links are kept.
            accounts: Where the accounts are kept.
            sign_in: Decides whether the account may sign in and links the identity.
            outbox: Sends the mail.
            mailings: Limits the mail to each address.
        """
        self._store = store
        self._accounts = accounts
        self._sign_in = sign_in
        self._outbox = outbox
        self._mailings = mailings

    async def send(
        self, account: Account, identity: Identity, service: str, language: str
    ) -> LinkSent | Wait:
        """Send an account's address the link that links an identity to it and signs in.

        Args:
            account: The account whose address the identity's service verified.
            identity: The identity.
            service: The name of the identity's service, which the mail names.
            language: The language code of the page it was asked from.

        Returns:
            The link that went out, or the wait while the last mail to the address is recent.
        """
        if wait := self._mailings.reserve(account.nuid):
            return wait
        token, link = new_link(VALID_FOR)
        await self._store.keep_proof(account.id, identity, link)
        name = account.display_name or account.nuid
        links = {"link": PATH.format(token=token)}
        times = {"expires": link.expires_at}
        values = {"service": service, "account": name}
        self._outbox.send(account.nuid, "account.mail.proof", language, links, times, **values)
        return LinkSent(account.nuid, link.expires_at)

    async def find(self, token: str) -> ProofFound | ProofGone:
        """Return what a proof link links, without using it.

        Args:
            token: The token the link carries.
        """
        return await self._working(await self._store.proof_of(digest(token)))

    async def use(self, token: str) -> ProofOpened | ProofGone:
        """Use a proof link: link its identity to its account, which may then sign in.

        A link works once, whatever comes of it.

        Args:
            token: The token the link carries.

        Raises:
            SignInRefused: When the account may not sign in; nothing is linked then.
        """
        found = await self._working(await self._store.take_proof(digest(token)))
        if isinstance(found, ProofGone):
            return found
        await self._sign_in.admit(found.account)
        linked = await self._sign_in.link(found.account, found.identity)
        return ProofOpened(found.account, found.identity, linked)

    async def _working(self, kept: KeptProof | None) -> ProofFound | ProofGone:
        if kept is None:
            return ProofGone(None)
        account = await self._accounts.account(kept.account_id)
        if kept.expired or account is None:
            return ProofGone(kept.identity.service)
        return ProofFound(account, kept.identity)
