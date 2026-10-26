import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, Self
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import QueryParams
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.entitlements import KeyHistory, StoredKey
from openfesl.core.games import Game, Games, ProductKey
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions, back_to

SECTION = "keys"
TABS = ("all", "enabled", "disabled")
# Codes are typed into the game's key prompt and name the key in the panel's addresses.
MAXIMUM_CODE = 64
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")


@dataclass(frozen=True, slots=True)
class KeyView:
    """A product key as the admin panel shows it.

    Attributes:
        key: The key.
        game: The game whose data lists it among its starting keys, if any.
        history: When the admin added it and last enabled or disabled it.
    """

    key: StoredKey
    game: Game | None
    history: KeyHistory = field(default_factory=KeyHistory)

    @property
    def tab(self) -> str:
        """The tab of the keys page that lists it besides all: enabled or disabled."""
        return "enabled" if self.key.enabled else "disabled"


def key_views(
    games: Games, keys: Sequence[StoredKey], history: dict[str, KeyHistory] | None = None
) -> list[KeyView]:
    """Return the views of product keys, each with the game that lists it, in their order.

    Args:
        games: The games the server serves.
        keys: The keys.
        history: When the admin added and changed them, by code.
    """
    starting = {key.code.upper(): game for game in games for key in game.keys}
    known = history or {}
    return [
        KeyView(key, starting.get(key.code.upper()), known.get(key.code, KeyHistory()))
        for key in keys
    ]


class KeyStore(Protocol):
    """Where the admin panel reads and changes the product keys."""

    async def keys(self) -> list[StoredKey]:
        """Return every product key with what it grants, in the order they were added."""

    async def key_history(self) -> dict[str, KeyHistory]:
        """Return when each product key was added and last enabled or disabled by the admin."""

    async def add_key(self, code: str, admin: Admin) -> bool:
        """Add an enabled product key that grants nothing yet.

        Args:
            code: The code entered in the game.
            admin: The admin who added it.

        Returns:
            Whether it was added, which it is not when a key has the code in any case.
        """

    async def enable(self, code: str, enabled: bool, admin: Admin) -> bool:
        """Enable or disable a product key.

        Args:
            code: The key as stored.
            enabled: Whether the game accepts it from now on.
            admin: The admin who changed it.

        Returns:
            Whether it changed.
        """

    async def import_keys(self, keys: Sequence[ProductKey], admin: Admin | None = None) -> int:
        """Add the keys whose codes do not exist yet, leaving existing keys untouched.

        Args:
            keys: Keys from the games' data files.
            admin: The admin who added them.

        Returns:
            How many keys were added.
        """


@dataclass(frozen=True, slots=True)
class KeyList:
    """Which product keys the keys page lists, as its address says.

    Attributes:
        tab: The tab: all, enabled or disabled.
        open: The key whose details are open, by its code.
    """

    tab: str = "all"
    open: str | None = None

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        tab = query.get("state", "")
        return cls(tab=tab if tab in TABS else "all", open=query.get("open") or None)

    def link(self, tab: str | None = None, open: str | None = None) -> str:
        """Return the query of the list's address in a tab with a key open, with a leading ?.

        Args:
            tab: The tab; this list's when None.
            open: The key whose details are open, if any.
        """
        shown = tab or self.tab
        fields = [("state", "" if shown == "all" else shown), ("open", open or "")]
        query = urlencode([(key, value) for key, value in fields if value])
        return f"?{query}" if query else ""


def key_routes(sessions: AdminSessions, games: Games, store: KeyStore) -> APIRouter:
    """Return the product keys page with adding, enabling and disabling keys.

    Moderators see the keys; changing them needs the admin role. Disabling asks first, which
    without scripts is a page of its own.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, whose data lists their starting keys.
        store: Where the product keys are kept.
    """
    router = APIRouter()

    async def views() -> list[KeyView]:
        return key_views(games, await store.keys(), await store.key_history())

    async def find(code: str) -> KeyView:
        view = next((view for view in await views() if view.key.code == code), None)
        if view is None:
            raise HTTPException(404)
        return view

    async def page(request: Request, admin: Admin, status: int = 200, **form: str) -> HTMLResponse:
        listing = KeyList.read(request.query_params)
        every = await views()
        known = {view.key.code.upper() for view in every}
        return await sessions.render(
            request,
            "admin.Keys",
            status,
            admin=admin,
            listing=listing,
            keys=[view for view in every if listing.tab in ("all", view.tab)],
            counts={tab: sum(tab in ("all", view.tab) for view in every) for tab in TABS},
            starting=[
                (game, sum(key.code.upper() in known for key in game.keys))
                for game in games
                if game.keys
            ],
            manages=admin.role is Role.ADMIN,
            longest=MAXIMUM_CODE,
            form=form,
        )

    @router.get("/admin/keys", response_class=HTMLResponse)
    async def keys(request: Request) -> HTMLResponse:
        return await page(request, sessions.admin(request))

    @router.post("/admin/keys")
    async def add(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        code = str(form.get("code", "")).strip()
        if len(code) > MAXIMUM_CODE or not _CODE.fullmatch(code):
            return await page(request, admin, 400, code=code, error="code")
        if not await store.add_key(code, admin):
            return await page(request, admin, 400, code=code, error="taken")
        sessions.toast(request, "web.admin.toast.key_added", code=code)
        return RedirectResponse(_listed(request, code), 303)

    @router.post("/admin/keys/starting-keys")
    async def starting(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        sessions.check(request, await request.form())
        added = await store.import_keys(games.keys, admin)
        if added:
            sessions.toast(request, "web.admin.toast.keys_imported", count=str(added))
        else:
            sessions.toast(request, "web.admin.toast.keys_imported_none")
        return RedirectResponse(_listed(request), 303)

    @router.get("/admin/keys/{code}/disable", response_class=HTMLResponse)
    async def confirm_disable(request: Request, code: str, back: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        return await sessions.render(
            request,
            "admin.KeyDisable",
            admin=admin,
            view=await find(code),
            back=back_to(request, back, "/admin/keys"),
        )

    for decision in ("enable", "disable"):
        router.add_api_route(
            f"/admin/keys/{{code}}/{decision}",
            _decide(sessions, store, find, decision == "enable"),
            methods=["POST"],
        )
    return router


def _listed(request: Request, open: str | None = None) -> str:
    target = localize("/admin/keys", request.state.language)
    return f"{target}{KeyList().link(open=open)}" + (f"#key-{open}" if open else "")


def _decide(
    sessions: AdminSessions,
    store: KeyStore,
    find: Callable[[str], Awaitable[KeyView]],
    enabled: bool,
) -> Callable[[Request, str], Awaitable[Response]]:
    toast = "web.admin.toast.key_enabled" if enabled else "web.admin.toast.key_disabled"

    async def decide(request: Request, code: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        view = await find(code)
        back = back_to(request, str(form.get("back", "")), "/admin/keys")
        if not enabled and form.get("confirmed") != "1":
            target = localize(f"/admin/keys/{code}/disable", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'back': back})}", 303)
        if await store.enable(view.key.code, enabled, admin):
            sessions.toast(request, toast, code=code)
        else:
            sessions.toast(request, "web.admin.toast.key_unchanged", error=True, code=code)
        return RedirectResponse(back, 303)

    return decide
