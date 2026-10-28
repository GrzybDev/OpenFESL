from dataclasses import dataclass

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.games import Game, Games
from openfesl.core.personas import PersonaStore
from openfesl.core.problems import Problem, display_name_problem


@dataclass(frozen=True, slots=True)
class Played:
    """The names an account plays under in one game.

    Attributes:
        game: The game.
        names: The names of its personas there, the oldest first.
    """

    game: Game
    names: list[str]


@dataclass(frozen=True, slots=True)
class Suggestion:
    """A name the account plays under that fits as its display name and is free.

    Attributes:
        name: The name.
        game: The game it plays under the name in.
    """

    name: str
    game: Game


class DisplayNames:
    """Gives accounts their display names, at the first web sign-in or from the account page."""

    def __init__(self, accounts: AccountStore, personas: PersonaStore, games: Games) -> None:
        """Name accounts from the store.

        Args:
            accounts: Where the accounts are kept.
            personas: Where their personas are kept, whose names make suggestions.
            games: The games, the one an account was made in first.
        """
        self._accounts = accounts
        self._personas = personas
        self._games = games

    async def played(self, account: Account) -> list[Played]:
        """Return the names an account plays under, by game, the game it was made in first.

        Args:
            account: The account.
        """
        games = sorted(self._games, key=lambda game: game.id != account.made_in)
        return [
            Played(game, [persona.name for persona in personas])
            for game in games
            if (personas := await self._personas.of_account(account.id, game.namespace))
        ]

    async def suggestion(self, played: list[Played]) -> Suggestion | None:
        """Return the first name an account plays under that fits the rules and is free.

        Args:
            played: The names it plays under, by game.
        """
        for entry in played:
            for name in entry.names:
                fits = display_name_problem(name) is None
                if fits and not await self._accounts.display_name_taken(name):
                    return Suggestion(name, entry.game)
        return None

    async def choose(self, account: Account, name: str) -> Problem | None:
        """Give an account the display name it chose, first or in place of its earlier one.

        Args:
            account: The account.
            name: The display name.

        Returns:
            What is wrong with the name, if anything; nothing is saved then.
        """
        if problem := display_name_problem(name):
            return problem
        if not await self._accounts.name_account(account.id, name):
            return Problem("display_name", "taken", name)
        return None
