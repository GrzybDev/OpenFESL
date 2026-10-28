from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from openfesl.core.games import Credits, Game, Scope
from openfesl.core.personas import Persona

# Games keep a balance in a signed 32-bit integer, so no balance may pass this.
MOST_BALANCE = 2**31 - 1


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

    async def peek(self, wallets: Sequence[Wallet]) -> list[int]:
        """Return the balances of wallets without opening any; a new one shows its allowance.

        Args:
            wallets: The wallets.
        """

    async def earned(self, wallets: Sequence[Wallet]) -> list[int]:
        """Return what wallets ever received, from their ledgers, without opening any.

        A new wallet counts the allowance it opens with.

        Args:
            wallets: The wallets.
        """

    async def credit_from_server(
        self, wallet: Wallet, amount: int, transaction: str, credits: Credits
    ) -> int | None:
        """Credit a wallet with what a dedicated server sends, up to the most its window allows.

        Args:
            wallet: The wallet.
            amount: The amount the server sends.
            transaction: The id the server gave the credit.
            credits: What servers may credit to the wallet within a window.

        Returns:
            The balance, or None when the ledger holds the transaction id already.
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
        wallets = [_wallet(game, persona, code) for code in game.currencies]
        return dict(zip(game.currencies, await self._store.balances(wallets), strict=True))

    async def credit_from_server(
        self, game: Game, persona: Persona, code: str, amount: int, transaction: str
    ) -> int | None:
        """Credit what a dedicated server says a persona earned, as far as the currency allows.

        Args:
            game: The persona's game.
            persona: The persona, whose data the server keeps.
            code: The currency, one that the game's servers may credit.
            amount: The amount the server sends.
            transaction: The id the server gave the credit, which is credited once.

        Returns:
            The balance, or None when the credit with that id was credited already.
        """
        credits = game.currencies[code].credits
        assert credits is not None
        return await self._store.credit_from_server(
            _wallet(game, persona, code), amount, transaction, credits
        )

    async def of_personas(
        self, game: Game, personas: Sequence[Persona], code: str
    ) -> list[tuple[int, int]]:
        """Return what personas have of a currency they each hold, and earned, opening nothing.

        Args:
            game: The personas' game.
            personas: The personas.
            code: The currency, which each persona holds on its own.
        """
        allowance = game.currencies[code].allowance
        wallets = [
            Wallet(game.id, code, persona.account_id, persona.id, allowance) for persona in personas
        ]
        balances = await self._store.peek(wallets)
        return list(zip(balances, await self._store.earned(wallets), strict=True))

    async def of_account(self, game: Game, account_id: int, codes: Sequence[str]) -> list[int]:
        """Return what an account has to spend in currencies its personas share, opening nothing.

        A wallet not opened yet shows the allowance it opens with, which it then holds.

        Args:
            game: The game.
            account_id: The account.
            codes: The currencies, each one that the account's personas share.
        """
        wallets = [
            Wallet(game.id, code, account_id, None, game.currencies[code].allowance)
            for code in codes
        ]
        return await self._store.peek(wallets)


def _wallet(game: Game, persona: Persona, code: str) -> Wallet:
    # A persona spends its account's balance in a currency that the account's personas share.
    currency = game.currencies[code]
    owner = persona.id if currency.scope is Scope.PERSONA else None
    return Wallet(game.id, code, persona.account_id, owner, currency.allowance)
