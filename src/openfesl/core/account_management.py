from dataclasses import dataclass
from datetime import datetime

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.games import Game, Games
from openfesl.core.personas import Persona, PersonaStore
from openfesl.core.problems import Problem
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
        games: Every game with the account's personas in it, the game it was made in first.
    """

    account: Account
    created: datetime
    games: list[GamePersonas]


class AccountManagement:
    """What a signed-in player sees of their account on the site and changes about it."""

    def __init__(self, accounts: AccountStore, personas: PersonaStore, games: Games) -> None:
        """Manage the accounts in the stores.

        Args:
            accounts: Where the accounts are kept.
            personas: Where their personas are kept.
            games: The games, each of which the overview names with the account's personas.
        """
        self._accounts = accounts
        self._personas = personas
        self._games = games

    async def overview(self, account_id: int) -> AccountOverview | None:
        """Return what the player of an account sees of it; None for an unknown account.

        Args:
            account_id: The account.
        """
        record = await self._accounts.record(account_id)
        if record is None:
            return None
        account = record.account
        games = sorted(self._games, key=lambda game: game.id != account.made_in)
        return AccountOverview(
            account,
            record.created,
            [
                GamePersonas(game, await self._personas.of_account(account.id, game.namespace))
                for game in games
            ],
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
