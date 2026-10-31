from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openfesl.core.account_rules import account_rules
from openfesl.core.admins import Admin
from openfesl.core.games import Credits, Currency, Game, Scope
from openfesl.core.personas import Persona
from openfesl.core.policies import SettingStore

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

    async def opened(self, wallet: Wallet) -> datetime | None:
        """Return when a wallet received its allowance, if its ledger records one.

        Args:
            wallet: The wallet.
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


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """A change of a wallet as its ledger keeps it, with what caused it where a record says.

    Attributes:
        id: Its number; later changes have higher ones.
        at: When it was made.
        change: What it added, below 0 for what it took.
        balance: The wallet's balance after it.
        reason: Why, as the ledger keeps it, such as purchase.
        purchase: The number of the purchase that paid it, if one did.
        items: The items that purchase gave, by number.
        admin: The name of the admin who made it, for an admin's grant or take.
        note: The admin's note.
    """

    id: int
    at: datetime
    change: int
    balance: int
    reason: str
    purchase: int | None = None
    items: tuple[int, ...] = ()
    admin: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Ledger:
    """A page of a wallet's ledger, newest first.

    Attributes:
        total: How many changes the reasons chosen let through.
        counts: How many changes each reason has in all.
        entries: The changes of the page.
    """

    total: int
    counts: Mapping[str, int]
    entries: list[LedgerEntry]


@dataclass(frozen=True, slots=True)
class WalletHead:
    """Whether a wallet was opened, and its latest change.

    Attributes:
        opened: Whether its first use opened it.
        last: Its latest change, if it has any.
    """

    opened: bool
    last: LedgerEntry | None


class WalletLedgers(Protocol):
    """Where the admin panel reads the wallets' ledgers."""

    async def ledger(
        self, wallet: Wallet, reasons: Collection[str], offset: int, limit: int
    ) -> Ledger:
        """Return a page of a wallet's ledger, newest first, without opening the wallet.

        Args:
            wallet: The wallet.
            reasons: The reasons whose changes are listed; every one when empty.
            offset: How many changes to skip.
            limit: The most changes to return.
        """

    async def heads(self, wallets: Sequence[Wallet]) -> list[WalletHead]:
        """Return whether each wallet was opened, with its latest change, opening none.

        Args:
            wallets: The wallets.
        """

    async def change(
        self, wallet: Wallet, amount: int, hero: str | None, note: str, admin: Admin
    ) -> tuple[int, bool]:
        """Grant to a wallet or take from it, opening it first with its allowance.

        The balance, its ledger entry and the admin's log change together, and only while the
        balance stays between 0 and the most a game holds.

        Args:
            wallet: The wallet.
            amount: What to add, below 0 to take.
            hero: The name of the hero whose own wallet it is, which the log keeps.
            note: Why, as the admin wrote it.
            admin: The admin who changed it.

        Returns:
            The balance before, and whether it changed.
        """


def wallet_of(game: Game, persona: Persona, code: str) -> Wallet:
    """Return the wallet from which a persona spends a currency of its game.

    A persona spends its account's balance in a currency that the account's personas share,
    and its own in the others.

    Args:
        game: The persona's game.
        persona: The persona.
        code: The currency.
    """
    currency = game.currencies[code]
    owner = persona.id if currency.scope is Scope.PERSONA else None
    return Wallet(game.id, code, persona.account_id, owner, currency.allowance)


class Wallets:
    """The balances that players spend in the currencies of their games."""

    def __init__(self, store: WalletStore, settings: SettingStore) -> None:
        """Keep the balances in a store.

        Args:
            store: Where the wallets are kept.
            settings: The instance's settings, which override the allowances and credits of the
                games' data.
        """
        self._store = store
        self._settings = settings

    async def currencies(self, game: Game) -> dict[str, Currency]:
        """Return a game's currencies, with the allowances and credits the instance sets.

        Args:
            game: The game.
        """
        return (await account_rules(game, self._settings)).currencies

    async def of_persona(self, game: Game, persona: Persona) -> dict[str, int]:
        """Return what a persona has to spend in each currency of its game, by code.

        A wallet opens on its first use with the currency's allowance, so an account or a
        persona gets each allowance once, whatever made the persona.

        Args:
            game: The persona's game.
            persona: The persona.
        """
        game = await account_rules(game, self._settings)
        wallets = [wallet_of(game, persona, code) for code in game.currencies]
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
        game = await account_rules(game, self._settings)
        credits = game.currencies[code].credits
        assert credits is not None
        return await self._store.credit_from_server(
            wallet_of(game, persona, code), amount, transaction, credits
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
        allowance = (await self.currencies(game))[code].allowance
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
        currencies = await self.currencies(game)
        wallets = [
            Wallet(game.id, code, account_id, None, currencies[code].allowance) for code in codes
        ]
        return await self._store.peek(wallets)

    async def allowance_given(self, game: Game, account_id: int, code: str) -> datetime | None:
        """Return when an account received the allowance of a currency, if it did.

        Args:
            game: The game.
            account_id: The account.
            code: The currency, one that the account's personas share.
        """
        allowance = (await self.currencies(game))[code].allowance
        return await self._store.opened(Wallet(game.id, code, account_id, None, allowance))
