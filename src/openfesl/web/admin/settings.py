from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.games import Game, Games
from openfesl.core.policies import GamePolicies, Policies, SettingStore, setting_key
from openfesl.core.servers import HostingMode
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions, SignInProvider
from openfesl.web.admin.servers import ServerViews

SECTION = "settings"


class HostingSetting(Protocol):
    """Where the hosting mode is kept."""

    async def hosting_mode(self) -> HostingMode:
        """Return whose games players see."""

    async def set_hosting_mode(self, mode: HostingMode, admin: Admin | None) -> None:
        """Change whose games players see.

        Args:
            mode: The new mode.
            admin: The admin who changed it.
        """


@dataclass(frozen=True, slots=True)
class PolicyView:
    """One of a game's policies as the settings page shows it.

    Attributes:
        name: Its name, such as accounts.concurrent_login.
        kind: How it is set: switch for on or off, choice among options, or text.
        value: Its value as the settings write it.
        default: The value the game's data gives it.
        options: The values a choice takes.
    """

    name: str
    kind: str
    value: str
    default: str
    options: tuple[str, ...] = ()

    @property
    def section(self) -> str:
        """The section it belongs to, such as accounts."""
        return self.name.partition(".")[0]


def policy_views(game: Game, current: Policies) -> list[PolicyView]:
    """Return a game's policies as the settings page shows them, in the data's order.

    Args:
        game: The game.
        current: Its policies with the instance's overrides.
    """
    defaults = game.policies
    views = []
    for name in defaults.names():
        section, _, field = name.partition(".")
        sample = defaults.model_dump()[section][field]
        options: tuple[str, ...] = ()
        if isinstance(sample, bool):
            kind = "switch"
        elif isinstance(sample, StrEnum):
            kind, options = "choice", tuple(option.value for option in type(sample))
        else:
            kind = "text"
        views.append(PolicyView(name, kind, current.text(name), defaults.text(name), options))
    return views


def settings_routes(
    sessions: AdminSessions,
    games: Games,
    policies: GamePolicies,
    store: SettingStore,
    hosting: HostingSetting,
    servers: ServerViews,
    apply: Callable[[], Awaitable[None]],
    provider: SignInProvider | None,
) -> APIRouter:
    """Return the settings page: who can host, each game's policies and the sign-in provider.

    Moderators see the settings; changing them needs the admin role. A change of the hosting
    mode asks first and applies to the games hosted now too; policies apply from their next
    use.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, each with its policies.
        policies: Reads each game's policies with the instance's overrides.
        store: Where the overrides are kept.
        hosting: Where the hosting mode is kept.
        servers: Counts the servers waiting for a decision.
        apply: Lists, hides or ends the servers' games as the hosting mode now says.
        provider: The admin panel's sign-in provider, if the deployment sets one up.
    """
    router = APIRouter()

    async def page(
        request: Request, admin: Admin, status: int = 200, errors: dict[str, str] | None = None
    ) -> HTMLResponse:
        mode = await hosting.hosting_mode()
        waiting = await servers.waiting(await servers.all())
        shown = [(game, policy_views(game, await policies.of(game))) for game in games]
        return await sessions.render(
            request,
            "admin.Settings",
            status,
            admin=admin,
            mode=mode.value,
            modes=[HostingMode.APPROVED.value, HostingMode.OPEN.value],
            waiting=len(waiting),
            games=shown,
            provider=provider,
            manages=admin.role is Role.ADMIN,
            errors=errors or {},
        )

    @router.get("/admin/settings", response_class=HTMLResponse)
    async def settings(request: Request) -> HTMLResponse:
        return await page(request, sessions.admin(request))

    @router.get("/admin/settings/hosting", response_class=HTMLResponse)
    async def confirm_mode(request: Request, mode: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        if mode not in set(HostingMode):
            raise HTTPException(404)
        return await sessions.render(
            request,
            "admin.HostingChange",
            admin=admin,
            mode=mode,
            earlier=(await hosting.hosting_mode()).value,
            waiting=len(await servers.waiting(await servers.all())),
            back=localize("/admin/settings", request.state.language),
        )

    @router.post("/admin/settings/hosting")
    async def change_mode(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        chosen = str(form.get("mode", ""))
        back = localize("/admin/settings", request.state.language)
        if chosen not in set(HostingMode) or chosen == await hosting.hosting_mode():
            sessions.toast(request, "web.admin.toast.mode_unchanged")
            return RedirectResponse(back, 303)
        if form.get("confirmed") != "1":
            target = localize("/admin/settings/hosting", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'mode': chosen})}", 303)
        await hosting.set_hosting_mode(HostingMode(chosen), admin)
        await apply()
        sessions.toast(request, f"web.admin.toast.mode.{chosen}")
        return RedirectResponse(back, 303)

    @router.post("/admin/settings/games/{game_id}")
    async def change_policies(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        sessions.check(request, form)
        game = games.by_id(game_id)
        if game is None:
            raise HTTPException(404)
        wanted, errors = _read(game, form)
        if errors:
            return await page(request, admin, 400, errors)
        current = await policies.of(game)
        changed = 0
        for name, value in wanted.items():
            if value == current.text(name):
                continue
            override = None if value == game.policies.text(name) else value
            await store.change(setting_key(game, name), override, admin)
            changed += 1
        if changed:
            sessions.toast(request, "web.admin.toast.policies", game=game.name)
        else:
            sessions.toast(request, "web.admin.toast.policies_unchanged", game=game.name)
        target = localize("/admin/settings", request.state.language)
        return RedirectResponse(f"{target}#game-{game.id}", 303)

    return router


def _read(game: Game, form: FormData) -> tuple[dict[str, str], dict[str, str]]:
    # Each policy's value from the form, by name, and which of them the game takes no such
    # value for.
    wanted: dict[str, str] = {}
    errors: dict[str, str] = {}
    for view in policy_views(game, game.policies):
        if view.kind == "switch":
            value = "on" if form.get(view.name) == "on" else "off"
        else:
            value = str(form.get(view.name, "")).strip()
        try:
            game.policies.overridden(view.name, value)
        except ValueError:
            errors[view.name] = value
        wanted[view.name] = value
    return wanted, errors
