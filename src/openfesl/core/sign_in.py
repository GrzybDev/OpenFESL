from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

from openfesl.core.accounts import STATE_ERRORS, Account, AccountState, AccountStore
from openfesl.core.fesl import TransactionError
from openfesl.core.games import Games
from openfesl.core.identities import Identity, IdentityStore, LinkedIdentity
from openfesl.core.limits import Attempts, Mailings, Wait
from openfesl.core.logins import Logins
from openfesl.core.policies import GamePolicies
from openfesl.core.verification import EmailVerification


class Refusal(Enum):
    """Why a sign-in on the site's pages started no session."""

    WRONG = auto()
    """No account has the address or the game password is not its own, which are never told
    apart, so the answer reveals no account."""
    PAUSED = auto()
    """Too many attempts with the address failed lately."""
    UNCONFIRMED = auto()
    """The account waits for the link that confirms its address."""
    DEACTIVATED = auto()
    """A deletion request took the account out of use."""


class SignInRefused(Exception):
    """Refuses a sign-in on the site's pages."""

    def __init__(self, refusal: Refusal, account: Account | None = None) -> None:
        """Refuse a sign-in for a reason.

        Args:
            refusal: Why it was refused.
            account: The account that proved to be the visitor's, for refusals that only such
                a proof reveals.
        """
        super().__init__(refusal)
        self.refusal = refusal
        self.account = account


@dataclass(frozen=True, slots=True)
class LinkSent:
    """A link that went to an account's address.

    Attributes:
        address: The address it went to.
        expires: When it stops working.
    """

    address: str
    expires: datetime


@dataclass(frozen=True, slots=True)
class ProofNeeded:
    """A new identity whose address, verified by its service, belongs to an account.

    A proof that the account is the visitor's links the identity to it.

    Attributes:
        account: The account.
    """

    account: Account


@dataclass(frozen=True, slots=True)
class ProofTarget:
    """An account that a new identity is to be linked to, with the ways it can be proved.

    Attributes:
        account: The account.
        password: Whether it has a game password.
        linked: The identities linked to it, through whose services it can be proved.
    """

    account: Account
    password: bool
    linked: list[LinkedIdentity]


class WebSignIn:
    """Decides whether an account signs in on the site's pages."""

    def __init__(
        self,
        accounts: AccountStore,
        identities: IdentityStore,
        logins: Logins,
        policies: GamePolicies,
        games: Games,
        verification: EmailVerification,
        attempts: Attempts,
        mailings: Mailings,
    ) -> None:
        """Check sign-ins against the accounts and the instance's policies.

        Args:
            accounts: Where the accounts are kept.
            identities: Where the identities at sign-in services linked to them are kept.
            logins: Checks game passwords.
            policies: The site's and each game's policies, which say whether an account waits
                for its address to be confirmed.
            games: The games, which the accounts they registered name.
            verification: Sends the links that confirm addresses.
            attempts: Counts the failed attempts with each address.
            mailings: Limits the mail to each address.
        """
        self._accounts = accounts
        self._identities = identities
        self._logins = logins
        self._policies = policies
        self._games = games
        self._verification = verification
        self._attempts = attempts
        self._mailings = mailings

    async def with_password(self, address: str, password: str) -> Account:
        """Return the account that an e-mail address and its game password sign in.

        Args:
            address: The e-mail address the account logs in with.
            password: The account's game password.

        Raises:
            SignInRefused: When sign-in with the address is paused, the address and password
                match no account, or the account may not sign in.
        """
        if self._attempts.paused(address):
            raise SignInRefused(Refusal.PAUSED)
        try:
            credentials = await self._logins.verify(address, password)
        except TransactionError:
            self._attempts.failed(address)
            raise SignInRefused(Refusal.WRONG) from None
        self._attempts.clear(address)
        await self.admit(credentials.account)
        return credentials.account

    async def with_service(self, identity: Identity) -> Account | ProofNeeded | None:
        """Return the account that an identity at a sign-in service is linked to.

        Sign-ins through services are never paused, as the service checked who signs in. A
        service's verified address only finds the account that a proof may link the identity to;
        it never signs in on its own.

        Args:
            identity: Who signed in through the service.

        Returns:
            The account; the account that a proof may link the identity to, when the service
            verified its address and the account has no identity of the service yet; None for
            any other identity that no account uses yet.

        Raises:
            SignInRefused: When the account may not sign in.
        """
        account_id = await self._identities.account_of(identity.service, identity.subject)
        account = await self._accounts.account(account_id) if account_id is not None else None
        if account is not None:
            await self.admit(account)
            return account
        found = await self._accounts.find(identity.address) if identity.address else None
        if found is None or found.account.server:
            return None
        linked = await self._identities.linked(found.account.id)
        if any(other.service == identity.service for other in linked):
            return None
        return ProofNeeded(found.account)

    async def proof_target(self, account_id: int) -> ProofTarget | None:
        """Return an account that a new identity is to be linked to, with its ways in.

        Args:
            account_id: The account.
        """
        account = await self._accounts.account(account_id)
        found = await self._accounts.find(account.nuid) if account else None
        if found is None:
            return None
        linked = await self._identities.linked(found.account.id)
        return ProofTarget(found.account, found.password_hash is not None, linked)

    async def link(self, account: Account, identity: Identity) -> bool:
        """Link an identity to an account that proved to be the visitor's.

        Args:
            account: The account, which a proof showed to be the visitor's.
            identity: The identity.

        Returns:
            Whether it was linked, which it is not when it was linked elsewhere meanwhile.
        """
        return await self._identities.link(account.id, identity)

    async def admit(self, account: Account) -> None:
        """Check that an account that proved to be the visitor's may sign in.

        Args:
            account: The account.

        Raises:
            SignInRefused: When a deletion request deactivated the account, or it waits for its
                address to be confirmed while the policy it was registered under asks for that.
        """
        if account.login_error() == STATE_ERRORS[AccountState.DEACTIVATED]:
            raise SignInRefused(Refusal.DEACTIVATED, account)
        if account.verification_expires_at is not None and await self._confirms(account):
            raise SignInRefused(Refusal.UNCONFIRMED, account)

    async def waiting(self, account_id: int) -> Account | None:
        """Return an account while it waits for its address to be confirmed.

        Args:
            account_id: The account, which proved to be the visitor's.
        """
        account = await self._accounts.account(account_id)
        return account if account and account.verification_expires_at else None

    async def send_confirmation(self, account_id: int, language: str) -> LinkSent | Wait | None:
        """Send an account that waits for its address to be confirmed a new link.

        Args:
            account_id: The account, which proved to be the visitor's.
            language: The language code of the page it was asked from.

        Returns:
            The link that went out, the wait while the last mail to the address is recent, or
            None once the account no longer waits.
        """
        account = await self.waiting(account_id)
        if account is None:
            return None
        if wait := self._mailings.reserve(account.nuid):
            return wait
        expires = await self._verification.resend(account, language)
        if expires is None:
            return None
        return LinkSent(account.nuid, expires)

    async def _confirms(self, account: Account) -> bool:
        # Only while the policy is on, so turning it off lets waiting accounts in, as games do.
        game = self._games.by_id(account.made_in) if account.made_in else None
        if game is None:
            return await self._policies.site_confirmation()
        return (await self._policies.of(game)).accounts.email_verification
