from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.fesl import TransactionError
from openfesl.core.games import Game, Games
from openfesl.core.identities import Identity, IdentityStore, LinkedIdentity
from openfesl.core.limits import Attempts
from openfesl.core.logins import Logins
from openfesl.core.mail import Outbox
from openfesl.core.personas import Persona, PersonaStore
from openfesl.core.problems import Problem, password_problem
from openfesl.core.registration import is_country


@dataclass(frozen=True, slots=True)
class GamePersonas:
    """The personas an account plays under in one game.

    Attributes:
        game: The game.
        personas: The personas, the oldest first; none for a game the account has not played.
    """

    game: Game
    personas: list[Persona]


class LinkRefusal(Enum):
    """Why an identity at a sign-in service was not linked to an account."""

    OTHER_ACCOUNT = auto()
    """The identity opens another account, which is not named."""
    SERVICE_LINKED = auto()
    """The account has another identity of the same service, and one is the most."""


@dataclass(frozen=True, slots=True)
class AccountOverview:
    """An account with what its player sees of it on the site.

    Attributes:
        account: The account.
        created: When it was made.
        password: Whether it has a game password.
        linked: The identities at sign-in services linked to it, the oldest first.
        remembered: How many logins the games remember for it.
        games: Every game with the account's personas in it, the game it was made in first.
        ways_in: How many ways in it has: its game password and its identities at the
            configured services, as one of a service no longer configured signs nothing in.
    """

    account: Account
    created: datetime
    password: bool
    linked: list[LinkedIdentity]
    remembered: int
    games: list[GamePersonas]
    ways_in: int


class AccountManagement:
    """What a signed-in player sees of their account on the site and changes about it."""

    def __init__(
        self,
        accounts: AccountStore,
        identities: IdentityStore,
        personas: PersonaStore,
        logins: Logins,
        attempts: Attempts,
        outbox: Outbox,
        games: Games,
        services: Collection[str],
    ) -> None:
        """Manage the accounts in the stores.

        Args:
            accounts: Where the accounts are kept.
            identities: Where the identities at sign-in services linked to them are kept.
            personas: Where their personas are kept.
            logins: Checks and hashes game passwords.
            attempts: Counts the failed attempts with each address, which a wrong current game
                password adds to as a wrong one at sign-in does.
            outbox: Sends the mail that confirms a deletion request.
            games: The games, each of which the overview names with the account's personas.
            services: The ids of the configured sign-in services, whose identities alone sign
                in.
        """
        self._accounts = accounts
        self._identities = identities
        self._personas = personas
        self._logins = logins
        self._attempts = attempts
        self._outbox = outbox
        self._games = games
        self._services = services

    async def overview(self, account_id: int) -> AccountOverview | None:
        """Return what the player of an account sees of it; None for an unknown account.

        Args:
            account_id: The account.
        """
        record = await self._accounts.record(account_id)
        credentials = await self._accounts.find(record.account.nuid) if record else None
        if record is None or credentials is None:
            return None
        account = record.account
        games = sorted(self._games, key=lambda game: game.id != account.made_in)
        password = credentials.password_hash is not None
        linked = await self._identities.linked(account.id)
        return AccountOverview(
            account,
            record.created,
            password,
            linked,
            len(await self._accounts.remembered(account.id)),
            [
                GamePersonas(game, await self._personas.of_account(account.id, game.namespace))
                for game in games
            ],
            int(password) + sum(identity.service in self._services for identity in linked),
        )

    async def change_country(self, account: Account, country: str) -> Problem | None:
        """Change the country of an account, which games receive with its details.

        The birth date is not kept, so the new country's age limit is not checked again.

        Args:
            account: The account.
            country: The ISO code of the new country.

        Returns:
            What is wrong with the country, if anything; nothing is saved then.
        """
        if not is_country(country):
            return Problem("country", "missing")
        await self._accounts.change_country(account.id, country.upper())
        return None

    async def change_password(self, account: Account, current: str, password: str) -> list[Problem]:
        """Set an account's game password, or change it given the current one.

        A wrong current game password counts toward the pause of the account's address, as at
        sign-in, so the form cannot be used to guess it. The account's other web sessions and the
        logins the games remember end.

        Args:
            account: The account.
            current: The current game password; ignored while the account has none.
            password: The new game password.

        Returns:
            What is wrong with the values sent; nothing is saved then.
        """
        credentials = await self._accounts.find(account.nuid)
        problems = []
        if credentials and credentials.password_hash is not None:
            if self._attempts.paused(account.nuid):
                problems.append(Problem("current_password", "paused"))
            else:
                try:
                    await self._logins.verify(account.nuid, current)
                except TransactionError:
                    self._attempts.failed(account.nuid)
                    problems.append(Problem("current_password", "wrong"))
        if problem := password_problem(password):
            problems.append(problem)
        if not problems:
            await self._accounts.change_password(account.id, await self._logins.hash(password))
            self._attempts.clear(account.nuid)
        return problems

    async def remove_password(self, account: Account) -> bool:
        """Remove an account's game password, without which games refuse its logins.

        The account's other web sessions and the logins the games remember end.

        Args:
            account: The account.

        Returns:
            Whether it was removed, which it is not while it is the account's last way in.
        """
        return await self._accounts.remove_password(account.id, self._services)

    async def link(self, account: Account, identity: Identity) -> LinkRefusal | None:
        """Link an identity at a sign-in service to an account, which it signs in from then on.

        An identity opens one account only, and an account has one identity per service.

        Args:
            account: The account, signed in on the site.
            identity: Who signed in through the service.

        Returns:
            Why it was not linked; None once it is linked, as also when it was already.
        """
        if await self._identities.link(account.id, identity):
            return None
        owner = await self._identities.account_of(identity.service, identity.subject)
        if owner == account.id:
            return None
        return LinkRefusal.OTHER_ACCOUNT if owner is not None else LinkRefusal.SERVICE_LINKED

    async def unlink(self, account: Account, service: str) -> bool:
        """Unlink an account's identity at a sign-in service, unless it is the last way in.

        Args:
            account: The account.
            service: The service's id.

        Returns:
            Whether it was unlinked.
        """
        return await self._identities.unlink(account.id, service, self._services)

    async def sign_out_everywhere(self, account: Account) -> None:
        """End every web session of an account and the logins the games remember for it.

        Each game then asks for the game password once more on each PC.

        Args:
            account: The account.
        """
        await self._accounts.end_sessions(account.id)

    async def request_deletion(
        self, account: Account, name: str, language: str
    ) -> datetime | Problem:
        """Deactivate an account at its player's request, for an admin to delete it.

        Its web sessions end, games refuse its logins, and a mail to its address confirms the
        request; only an admin can withdraw it.

        Args:
            account: The account.
            name: The display name typed to confirm the request, which must be the account's.
            language: The language code of the page the request came from.

        Returns:
            When it was deactivated, or what is wrong with the name typed; nothing changes then.
        """
        if name != account.display_name:
            return Problem("confirm_name", "mismatch", name)
        deactivated = await self._accounts.request_deletion(account.id)
        if self._outbox.sends:
            times = {"at": deactivated}
            self._outbox.send(account.nuid, "account.mail.deletion", language, {}, times, name=name)
        return deactivated
