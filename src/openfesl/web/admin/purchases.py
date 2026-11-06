from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from math import ceil
from typing import Any, Protocol, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import QueryParams

from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Scope
from openfesl.core.purchases import ListedPurchase
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.store import StoreAdminViews
from openfesl.web.pages import page_number, query_of

PAGE_SIZE = 50


class PurchaseLog(Protocol):
    """Lists the purchases made in the games' stores."""

    async def listed(
        self,
        game: str,
        account_id: int | None,
        currency: str,
        since: datetime | None,
        until: datetime | None,
        who: str,
        offset: int,
        limit: int,
    ) -> tuple[dict[str, tuple[int, int]], list[ListedPurchase]]:
        """Return a game's purchases that the choices let through, the latest first.

        Args:
            game: The game's data-file name.
            account_id: The account that made them; any when none.
            currency: The currency they were paid in; any when empty.
            since: The moment from which they were made; any when none.
            until: The moment before which they were made; any when none.
            who: The start of the login name of the account or of the hero's name; any when
                empty.
            offset: How many purchases to skip.
            limit: The most to return.

        Returns:
            How many purchases each currency has and what they cost together, and those of the
            page.
        """


@dataclass(frozen=True, slots=True)
class PurchaseListing:
    """Which purchases the store's list shows, as its address says; what is empty does not filter.

    Attributes:
        currency: The currency they were paid in.
        start: The first day they were made on, in UTC, as a date field writes it.
        end: The last day, likewise.
        who: The start of the login name of the account or of the hero's name.
        account: The number of the account that made them.
        page: The page, from 1.
    """

    currency: str = ""
    start: str = ""
    end: str = ""
    who: str = ""
    account: int | None = None
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name or names wrongly is the default.

        Args:
            query: The address's query.
        """
        account = query.get("account", "")
        return cls(
            query.get("currency", "")[:16],
            _day(query.get("from", "")),
            _day(query.get("to", "")),
            query.get("who", "").strip()[:64],
            int(account) if account.isdigit() and len(account) < 19 else None,
            page_number(query.get("page", "")),
        )

    def changed(self, **changes: Any) -> PurchaseListing:
        """Return the list with some choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        return query_of(
            ("currency", self.currency),
            ("from", self.start),
            ("to", self.end),
            ("who", self.who),
            ("account", str(self.account) if self.account else ""),
            ("page", str(self.page) if self.page > 1 else ""),
        )


@dataclass(frozen=True, slots=True)
class PurchaseLine:
    """A purchase as a list of purchases shows it.

    Attributes:
        listed: The purchase with its account and hero.
        ledger: The path of the ledger of the wallet that paid it, with its purchases.
    """

    listed: ListedPurchase
    ledger: str


def purchase_lines(game: Game, found: list[ListedPurchase]) -> list[PurchaseLine]:
    """Return a game's purchases as its lists show them, each with the ledger that paid it.

    Args:
        game: The game.
        found: Its purchases.
    """
    lines = []
    for listed in found:
        kept = game.currencies.get(listed.currency)
        own = kept is not None and kept.scope is Scope.PERSONA and listed.persona_id is not None
        wallet = f"{listed.currency}-{listed.persona_id}" if own else listed.currency
        query = urlencode({"game": game.id, "wallet": wallet, "purchases": "1"})
        lines.append(PurchaseLine(listed, f"/admin/accounts/{listed.account_id}/ledger?{query}"))
    return lines


def purchase_routes(
    sessions: AdminSessions, views: StoreAdminViews, catalogues: Catalogues, log: PurchaseLog
) -> APIRouter:
    """Return every purchase of each game's store, which every role reads.

    The list is the newest first, 50 a page, filtered by currency, days, and the account or hero,
    with how many purchases the filter lets through and what they cost in each currency.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Name the games with stores.
        catalogues: Name the items that purchases gave.
        log: Lists the purchases.
    """
    router = APIRouter()

    @router.get("/admin/store/{game_id}/purchases", response_class=HTMLResponse)
    async def purchases(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        listing = PurchaseListing.read(request.query_params)
        if listing.currency not in game.currencies:
            listing = listing.changed(currency="")
        since = _start(listing.start)
        end = _start(listing.end)
        totals, found = await log.listed(
            game.id,
            listing.account,
            listing.currency,
            since,
            end + timedelta(days=1) if end else None,
            listing.who,
            (listing.page - 1) * PAGE_SIZE,
            PAGE_SIZE,
        )
        total = sum(count for count, _ in totals.values())
        return await sessions.render(
            request,
            "admin.StorePurchases",
            admin=admin,
            of=game,
            catalogue=await catalogues.of(game, request.state.language),
            listing=listing,
            lines=purchase_lines(game, found),
            totals=totals,
            total=total,
            pages=ceil(total / PAGE_SIZE),
            size=PAGE_SIZE,
        )

    return router


def _day(text: str) -> str:
    # A day as a date field writes it, such as 2026-10-31; another text is no day.
    try:
        return date.fromisoformat(text).isoformat() if len(text) == 10 else ""
    except ValueError:
        return ""


def _start(day: str) -> datetime | None:
    return datetime.combine(date.fromisoformat(day), time(), UTC) if day else None
