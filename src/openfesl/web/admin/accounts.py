from dataclasses import dataclass, replace
from math import ceil
from typing import Any, Protocol, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import QueryParams

from openfesl.core.accounts import AccountRecord, RememberedLogin
from openfesl.core.admins import Role
from openfesl.core.census import Census, Server
from openfesl.core.entitlements import Entitlement, EntitlementQuery, StatusChange
from openfesl.core.games import Game, Games
from openfesl.core.personas import PersonaRecord, PersonaState
from openfesl.core.session import GameSession, Players
from openfesl.web.admin.auth import AdminSessions

PAGE_SIZE = 25
# The status of an entitlement the admin did not take away.
ACTIVE = "ACTIVE"


class AccountDirectory(Protocol):
    """Finds the players' accounts and what is kept about them."""

    async def search(
        self, start: str, oldest_first: bool, offset: int, limit: int
    ) -> tuple[int, list[AccountRecord]]:
        """Return the accounts whose login name or a persona's name starts with a text.

        Args:
            start: The start of the name, matched regardless of case; empty matches every
                account.
            oldest_first: Whether the oldest accounts come first; otherwise the newest do.
            offset: How many matching accounts to skip.
            limit: The most accounts to return.

        Returns:
            How many accounts match, and those of the page.
        """

    async def record(self, account_id: int) -> AccountRecord | None:
        """Return a player's account by its number.

        Args:
            account_id: The account's number.
        """

    async def remembered(self, account_id: int) -> list[RememberedLogin]:
        """Return the logins remembered for an account that still log it in, newest first.

        Args:
            account_id: The account.
        """


class HeldEntitlements(Protocol):
    """Finds the entitlements accounts hold and when the admin last changed them."""

    async def of_account(self, account_id: int, query: EntitlementQuery) -> list[Entitlement]:
        """Return an account's entitlements, filtered by whichever filters are not empty.

        Args:
            account_id: The account.
            query: The filters.
        """

    async def changes(self, account_id: int) -> dict[str, StatusChange]:
        """Return when the admin last revoked or granted again each of an account's entitlements.

        Args:
            account_id: The account.
        """


class PersonaDirectory(Protocol):
    """Finds the personas of accounts."""

    async def records(self, account_ids: list[int]) -> dict[int, list[PersonaRecord]]:
        """Return the personas of some accounts in every namespace, oldest first, by account.

        Args:
            account_ids: The accounts.
        """


@dataclass(frozen=True, slots=True)
class PersonaView:
    """A persona as the admin panel lists it.

    Attributes:
        record: The persona.
        game: The game whose namespace it is in, when known.
        playing: Whether its player plays as it now.
        server: The server whose game it is in now, if any.
    """

    record: PersonaRecord
    game: Game | None
    playing: bool = False
    server: Server | None = None

    @property
    def usable(self) -> bool:
        """Whether its player may play as it: neither removed nor disabled by the admin."""
        return self.record.removed is None and self.record.state is PersonaState.ACTIVE


@dataclass(frozen=True, slots=True)
class AccountView:
    """A player's account as the admin panel shows it.

    Attributes:
        record: The account.
        personas: Its personas, oldest first.
        session: The session it is logged in on now, if any.
    """

    record: AccountRecord
    personas: list[PersonaView]
    session: GameSession | None

    @property
    def playing(self) -> PersonaView | None:
        """The persona its player plays as now, if any."""
        return next((view for view in self.personas if view.playing), None)


@dataclass(frozen=True, slots=True)
class AccountList:
    """Which accounts the accounts page lists, as its address says.

    Attributes:
        search: The start of a login name or a persona's name.
        oldest_first: Whether the oldest accounts come first.
        page: The page of the list, from 1.
    """

    search: str = ""
    oldest_first: bool = False
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        page = query.get("page", "")
        return cls(
            search=query.get("q", "").strip(),
            oldest_first=query.get("order") == "asc",
            page=int(page) if page.isdigit() and int(page) > 0 else 1,
        )

    def changed(self, **changes: Any) -> AccountList:
        """Return the list with some choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        fields = [
            ("q", self.search),
            ("order", "asc" if self.oldest_first else ""),
            ("page", str(self.page) if self.page > 1 else ""),
        ]
        query = urlencode([(key, value) for key, value in fields if value])
        return f"?{query}" if query else ""


class AccountViews:
    """Puts together what the admin panel shows about the players' accounts."""

    def __init__(
        self,
        games: Games,
        census: Census,
        players: Players,
        accounts: AccountDirectory,
        personas: PersonaDirectory,
    ) -> None:
        """Gather from the live sessions and the stored accounts and personas.

        Args:
            games: The games the server serves.
            census: Knows where players are.
            players: Who is logged in.
            accounts: Where the accounts are kept.
            personas: Where the personas are kept.
        """
        self._games = games
        self._census = census
        self._players = players
        self.accounts = accounts
        self._personas = personas

    async def views(self, records: list[AccountRecord]) -> list[AccountView]:
        """Return the views of some accounts, in their order.

        Args:
            records: The accounts.
        """
        personas = await self._personas.records([record.account.id for record in records])
        places = {game.id: self._census.places(game) for game in self._games}
        by_namespace = {game.namespace: game for game in self._games}
        views = []
        for record in records:
            session = self._players.online(record.account.id)
            playing = session.persona.id if session and session.persona else None
            listed = []
            for persona in personas[record.account.id]:
                game = by_namespace.get(persona.persona.namespace)
                here = persona.persona.id == playing
                server = places[game.id].get(persona.persona.id) if game and here else None
                listed.append(PersonaView(persona, game, here, server))
            views.append(AccountView(record, listed, session))
        return views

    async def find(self, account_id: str) -> AccountView | None:
        """Return a player's account by its number as an address names it.

        Args:
            account_id: The number.
        """
        record = await self.accounts.record(int(account_id)) if account_id.isdigit() else None
        return (await self.views([record]))[0] if record else None


def account_routes(
    sessions: AdminSessions, views: AccountViews, entitlements: HeldEntitlements, terms: str
) -> APIRouter:
    """Return the accounts page, with its search, and each account's page.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Puts together what the panel shows about the accounts.
        entitlements: Where the accounts' entitlements are kept.
        terms: The version of the service's terms that accounts accept now.
    """
    router = APIRouter()

    @router.get("/admin/accounts", response_class=HTMLResponse)
    async def accounts(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        listing = AccountList.read(request.query_params)
        total, found = await views.accounts.search(
            listing.search, listing.oldest_first, (listing.page - 1) * PAGE_SIZE, PAGE_SIZE
        )
        return await sessions.render(
            request,
            "admin.Accounts",
            admin=admin,
            listing=listing,
            accounts=await views.views(found),
            total=total,
            pages=ceil(total / PAGE_SIZE),
            size=PAGE_SIZE,
        )

    @router.get("/admin/accounts/{account_id}", response_class=HTMLResponse)
    async def account(request: Request, account_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        view = await views.find(account_id)
        if view is None:
            return await sessions.render(
                request, "admin.AccountMissing", 404, admin=admin, number=account_id
            )
        number = view.record.account.id
        held = await entitlements.of_account(number, EntitlementQuery())
        total, _found = await views.accounts.search("", False, 0, 0)
        return await sessions.render(
            request,
            "admin.Account",
            admin=admin,
            view=view,
            entitlements=held,
            active=sum(entitlement.status == ACTIVE for entitlement in held),
            changes=await entitlements.changes(number),
            remembered=await views.accounts.remembered(number),
            total=total,
            terms=terms,
            manages=admin.role is Role.ADMIN,
        )

    return router
