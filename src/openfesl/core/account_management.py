from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.fesl import TransactionError
from openfesl.core.games import Game, Games
from openfesl.core.identities import IdentityStore, LinkedIdentity
from openfesl.core.limits import Attempts
from openfesl.core.logins import Logins
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


@dataclass(frozen=True, slots=True)
class AccountOverview:
    """An account with what its player sees of it on the site.

    Attributes:
        account: The account.
        created: When it was made.
        password: Whether it has a game password.
        linked: The identities at sign-in services linked to it, the oldest first.
        games: Every game with the account's personas in it, the game it was made in first.
        ways_in: How many ways in it has: its game password and its identities at the
            configured services, as one of a service no longer configured signs nothing in.
    """

    account: Account
    created: datetime
    password: bool
    linked: list[LinkedIdentity]
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
            games: The games, each of which the overview names with the account's personas.
            services: The ids of the configured sign-in services, whose identities alone sign
                in.
        """
        self._accounts = accounts
        self._identities = identities
        self._personas = personas
        self._logins = logins
        self._attempts = attempts
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
