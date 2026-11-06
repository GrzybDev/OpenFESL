from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import ceil
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException

from openfesl.core.admins import Role
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games, Scope
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.texts import translate
from openfesl.core.wallets import MOST_BALANCE, Ledger, Wallet, WalletHead, WalletLedgers, Wallets
from openfesl.web.admin.accounts import AccountView, AccountViews
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.purchases import PurchaseLog, purchase_lines
from openfesl.web.pages import number, whole_number
from openfesl.web.sessions import checked_form, toast

SECTION = "accounts"
PAGE_SIZE = 50
# The ledger's reasons by the groups its filter offers.
REASONS = {
    "purchases": ("purchase", "rename", "hero-slot"),
    "matches": ("server",),
    "levels": ("level", "refund"),
    "rewards": ("reward", "code"),
    "admin": ("admin",),
    "start": ("allowance",),
}


@dataclass(frozen=True, slots=True)
class WalletLine:
    """A wallet of an account or of one of its heroes, as the admin panel lists it.

    Attributes:
        wallet: The wallet.
        hero: The hero whose own wallet it is; none for the account's.
        balance: What it holds, or would hold once opened.
        earned: What it ever received, for a hero's own wallet.
        head: Whether it was opened, with its latest change.
    """

    wallet: Wallet
    hero: Hero | None
    balance: int
    earned: int | None
    head: WalletHead

    @property
    def key(self) -> str:
        """The wallet's name in the ledger's address: its currency, and its hero's number."""
        return (
            f"{self.wallet.currency}-{self.hero.persona.id}" if self.hero else self.wallet.currency
        )


@dataclass(frozen=True, slots=True)
class GameWallets:
    """An account's wallets in one game: the account's own first, then each hero's.

    Attributes:
        game: The game.
        heroes: The account's heroes in it.
        default: The default hero, if any.
        lines: The wallets.
    """

    game: Game
    heroes: list[Hero]
    default: Hero | None
    lines: list[WalletLine]


@dataclass(frozen=True, slots=True)
class LedgerList:
    """Which page of which wallet's ledger the ledger page shows, as its address says.

    Attributes:
        game: The game's data-file name.
        wallet: The wallet's name, as a line's key gives it.
        groups: The groups of reasons listed; every one when empty.
        page: The page, from 1.
    """

    game: str = ""
    wallet: str = ""
    groups: tuple[str, ...] = ()
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the ledger's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        page = query.get("page", "")
        return cls(
            game=query.get("game", ""),
            wallet=query.get("wallet", ""),
            groups=tuple(group for group in REASONS if query.get(group)),
            page=int(page) if page.isdigit() and 0 < int(page) < 10**6 else 1,
        )

    def changed(self, **changes: Any) -> LedgerList:
        """Return the list with some choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with its leading ?."""
        fields = [("game", self.game), ("wallet", self.wallet)]
        fields += [(group, "1") for group in self.groups]
        fields += [("page", str(self.page))] if self.page > 1 else []
        return f"?{urlencode(fields)}"


def counted_change(
    form: Mapping[str, Any], now: int, takes: bool = True
) -> tuple[int, bool, str, dict[str, str]]:
    """Return what a form asks of a count, or the fields of the form to show again with why.

    Args:
        form: The form, with its amount, its note and whether it takes the amount or grants it.
        now: What the count holds.
        takes: Whether the form may take from the count, or only grant.

    Returns:
        The amount, whether it is taken, the note, and the fields of a refused form or none.
    """
    note = str(form.get("note", "")).strip()
    written = str(form.get("amount", "")).strip()
    taking = takes and form.get("direction") == "take"
    amount = whole_number(written, MOST_BALANCE) or 0
    after = now - amount if taking else now + amount
    error = "amount" if amount <= 0 else "fewer" if after < 0 else ""
    error = error or ("more" if after > MOST_BALANCE else "") or ("note" if not note else "")
    direction = "take" if taking else "grant"
    refused = {"amount": written, "note": note, "error": error, "direction": direction}
    return amount, taking, note, refused if error else {}


def shared_currencies(game: Game) -> list[str]:
    """Return the currencies that an account's heroes share, in the order its site lists them.

    Args:
        game: The game.
    """
    listed = [wallet.currency for wallet in game.site.wallets] if game.site else []
    shared = [code for code, kept in game.currencies.items() if kept.scope is Scope.ACCOUNT]
    return sorted(shared, key=lambda code: listed.index(code) if code in listed else len(listed))


class WalletAdminViews:
    """Puts together what the admin panel shows about the wallets of accounts and heroes."""

    def __init__(
        self, games: Games, heroes: Heroes, wallets: Wallets, ledgers: WalletLedgers
    ) -> None:
        """Gather from the heroes, their wallets and the wallets' ledgers.

        Args:
            games: The games, of which those whose heroes are made on their site have wallets.
            heroes: Lists each account's heroes.
            wallets: Reads the balances without opening a wallet.
            ledgers: Reads the wallets' ledgers.
        """
        self.games = [game for game in games if game.heroes and game.currencies]
        self._heroes = heroes
        self._wallets = wallets
        self.ledgers = ledgers

    async def of_account(self, account_id: int) -> list[GameWallets]:
        """Return an account's wallets and its heroes' in each game that has them.

        Args:
            account_id: The account.
        """
        found = []
        for game in self.games:
            roster = await self._heroes.roster(game, account_id)
            shared = shared_currencies(game)
            own = [code for code, kept in game.currencies.items() if kept.scope is Scope.PERSONA]
            balances = await self._wallets.of_account(game, account_id, shared)
            currencies = await self._wallets.currencies(game)
            lines: list[tuple[Wallet, Hero | None, int, int | None]] = [
                (
                    Wallet(game.id, code, account_id, None, currencies[code].allowance),
                    None,
                    held,
                    None,
                )
                for code, held in zip(shared, balances, strict=True)
            ]
            personas = [hero.persona for hero in roster.heroes]
            for code in own:
                points = await self._wallets.of_personas(game, personas, code)
                allowance = currencies[code].allowance
                lines += [
                    (
                        Wallet(game.id, code, account_id, hero.persona.id, allowance),
                        hero,
                        held,
                        earned,
                    )
                    for hero, (held, earned) in zip(roster.heroes, points, strict=True)
                ]
            heads = await self.ledgers.heads([line[0] for line in lines])
            found.append(
                GameWallets(
                    game,
                    roster.heroes,
                    roster.default,
                    [WalletLine(*line, head) for line, head in zip(lines, heads, strict=True)],
                )
            )
        return found


def wallet_routes(
    sessions: AdminSessions,
    accounts: AccountViews,
    views: WalletAdminViews,
    catalogues: Catalogues,
    purchases: PurchaseLog,
) -> APIRouter:
    """Return an account's wallets and its heroes', the ledger of each, and grants and takes.

    Every role reads them, with the account's latest purchases in each game's store, and
    looking never opens a wallet. A grant or a take needs the admin role and a note, and stays
    within what a game's balance holds.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        accounts: Puts together what the panel shows about the accounts.
        views: Puts together what the panel shows about the wallets.
        catalogues: Name the items that purchases gave.
        purchases: Lists the purchases.
    """
    router = APIRouter()

    @router.get("/admin/accounts/{account_id}/wallets", response_class=HTMLResponse)
    async def wallets(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request)
        view = await accounts.found(account_id)
        number = view.record.account.id
        found = await views.of_account(number)
        bought = {}
        for game in (each.game for each in found if each.game.store):
            totals, listed = await purchases.listed(
                game.id, number, "", None, None, "", 0, PAGE_SIZE
            )
            catalogue = await catalogues.of(game, request.state.language)
            count = sum(count for count, _ in totals.values())
            bought[game.id] = (count, purchase_lines(game, listed), catalogue)
        return await sessions.render(
            request,
            "admin.Wallets",
            admin=admin,
            view=view,
            found=found,
            bought=bought,
            size=PAGE_SIZE,
        )

    async def wallet_line(account_id: str, game: str, key: str) -> tuple[AccountView, WalletLine]:
        view = await accounts.found(account_id)
        for found in await views.of_account(view.record.account.id):
            line = next((line for line in found.lines if line.key == key), None)
            if found.game.id == game and line is not None:
                return view, line
        raise HTTPException(404)

    @router.get("/admin/accounts/{account_id}/wallets/{game}/{key}", response_class=HTMLResponse)
    async def ask_change(request: Request, account_id: str, game: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        view, line = await wallet_line(account_id, game, key)
        back = back_to(request, request.query_params.get("back", ""), _wallets(view))
        return await sessions.render(
            request, "admin.WalletChange", admin=admin, view=view, line=line, back=back, form={}
        )

    @router.post("/admin/accounts/{account_id}/wallets/{game}/{key}")
    async def change(request: Request, account_id: str, game: str, key: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        view, line = await wallet_line(account_id, game, key)
        back = back_to(request, str(form.get("back", "")), _wallets(view))
        amount, taking, note, refused = counted_change(form, line.balance)
        if refused:
            return await sessions.render(
                request,
                "admin.WalletChange",
                400,
                admin=admin,
                view=view,
                line=line,
                back=back,
                form=refused,
            )
        hero = line.hero.persona.name if line.hero else None
        change = -amount if taking else amount
        before, done = await views.ledgers.change(line.wallet, change, hero, note, admin)
        language = request.state.language
        values = {
            "amount": number(amount, language),
            "currency": translate(f"games.{game}.currency.{line.wallet.currency}", language),
            "owner": hero or view.record.account.nuid,
            "before": number(before, language),
            "after": number(before + change, language),
            "most": number(MOST_BALANCE, language),
        }
        done_key = "taken" if taking else "granted"
        not_done = "fewer" if taking else "more"
        key = f"web.admin.toast.wallet.{done_key if done else not_done}"
        toast(request, key, error=not done, **values)
        return RedirectResponse(back, 303)

    @router.get("/admin/accounts/{account_id}/ledger", response_class=HTMLResponse)
    async def ledger(request: Request, account_id: str) -> Response:
        admin = sessions.admin(request)
        view = await accounts.found(account_id)
        listing = LedgerList.read(request.query_params)
        found = await views.of_account(view.record.account.id)
        game = next((each for each in found if each.game.id == listing.game), None)
        if game is None and found and not listing.game:
            game = found[0]
        line = (
            next((each for each in game.lines if each.key == listing.wallet), None)
            if game
            else None
        )
        if game is None or (line is None and listing.wallet):
            raise HTTPException(404)
        line = line or game.lines[0]
        listing = listing.changed(game=game.game.id, wallet=line.key)
        reasons = [reason for group in listing.groups for reason in REASONS[group]]
        shown: Ledger = await views.ledgers.ledger(
            line.wallet, reasons, (listing.page - 1) * PAGE_SIZE, PAGE_SIZE
        )
        return await sessions.render(
            request,
            "admin.Ledger",
            admin=admin,
            view=view,
            found=game,
            line=line,
            listing=listing,
            ledger=shown,
            groups={
                group: sum(shown.counts.get(reason, 0) for reason in reasons)
                for group, reasons in REASONS.items()
            },
            pages=ceil(shown.total / PAGE_SIZE),
            size=PAGE_SIZE,
            catalogue=await catalogues.of(game.game, request.state.language),
        )

    return router


def _wallets(view: AccountView) -> str:
    return f"/admin/accounts/{view.record.account.id}/wallets"
