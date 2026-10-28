from enum import Enum, auto

from openfesl.core.accounts import STATE_ERRORS, Account, AccountState
from openfesl.core.fesl import TransactionError
from openfesl.core.games import Games
from openfesl.core.logins import Logins
from openfesl.core.policies import GamePolicies


class Refusal(Enum):
    """Why a sign-in on the site's pages started no session."""

    WRONG = auto()
    """No account has the address or the game password is not its own, which are never told
    apart, so the answer reveals no account."""
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


class WebSignIn:
    """Decides whether an account signs in on the site's pages."""

    def __init__(self, logins: Logins, policies: GamePolicies, games: Games) -> None:
        """Check sign-ins against the accounts and the instance's policies.

        Args:
            logins: Checks game passwords.
            policies: The site's and each game's policies, which say whether an account waits
                for its address to be confirmed.
            games: The games, which the accounts they registered name.
        """
        self._logins = logins
        self._policies = policies
        self._games = games

    async def with_password(self, address: str, password: str) -> Account:
        """Return the account that an e-mail address and its game password sign in.

        Args:
            address: The e-mail address the account logs in with.
            password: The account's game password.

        Raises:
            SignInRefused: When the address and password match no account, or the account may
                not sign in.
        """
        try:
            credentials = await self._logins.verify(address, password)
        except TransactionError:
            raise SignInRefused(Refusal.WRONG) from None
        await self.admit(credentials.account)
        return credentials.account

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

    async def _confirms(self, account: Account) -> bool:
        # Only while the policy is on, so turning it off lets waiting accounts in, as games do.
        game = self._games.by_id(account.made_in) if account.made_in else None
        if game is None:
            return await self._policies.site_confirmation()
        return (await self._policies.of(game)).accounts.email_verification
