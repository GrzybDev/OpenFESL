from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from openfesl.core.games import Game, Scope
from openfesl.core.personas import Persona


@dataclass(frozen=True, slots=True)
class Wallet:
    """An account's balance in a currency of a game, or one of its personas' own.

    Attributes:
        game: The game's data-file name.
        currency: The currency's code.
        account_id: The account that holds it.
        persona_id: The persona whose own balance it is; none for one that the account's
            personas share.
        allowance: What it starts with, which its ledger records when it is opened.
    """

    game: str
    currency: str
    account_id: int
    persona_id: int | None
    allowance: int


class WalletStore(Protocol):
    """Where the wallets and the ledgers of their changes are kept."""

    async def balances(self, wallets: Sequence[Wallet]) -> list[int]:
        """Return the balances of wallets, opening those that are new with their allowance.

        Args:
            wallets: The wallets.
        """


class Wallets:
    """The balances that players spend in the currencies of their games."""

    def __init__(self, store: WalletStore) -> None:
        """Keep the balances in a store.

        Args:
            store: Where the wallets are kept.
        """
        self._store = store

    async def of_persona(self, game: Game, persona: Persona) -> dict[str, int]:
        """Return what a persona has to spend in each currency of its game, by code.

        A persona spends its account's balance in a currency that the account's personas share,
        and its own in the others. A wallet opens on its first use with the currency's
        allowance, so an account or a persona gets each allowance once, whatever made the
        persona.

        Args:
            game: The persona's game.
            persona: The persona.
        """
        wallets = [
            Wallet(
                game.id,
                code,
                persona.account_id,
                persona.id if currency.scope is Scope.PERSONA else None,
                currency.allowance,
            )
            for code, currency in game.currencies.items()
        ]
        return dict(zip(game.currencies, await self._store.balances(wallets), strict=True))
