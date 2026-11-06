import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException

from openfesl.core.admin import ActionHistory, AdminAction, AuditEntry
from openfesl.core.admins import Admin, Role
from openfesl.core.calendar import checked_zone
from openfesl.core.game_icons import (
    MAX_ICON_SIZE,
    IconProblem,
    IconRefused,
    StoredIcon,
    icon_png,
    png_size,
)
from openfesl.core.game_marks import GameMarks
from openfesl.core.games import Game, Games
from openfesl.core.policies import GamePolicies, Policies, SettingStore, setting_key
from openfesl.core.servers import HostingMode
from openfesl.core.texts import localize, translate
from openfesl.web.admin.auth import AdminSessions, SignInProvider, game_among
from openfesl.web.admin.rewards import RewardAdminViews
from openfesl.web.admin.servers import ServerViews
from openfesl.web.game_icons import ICON_PATH, icon_version, reaches_discord
from openfesl.web.pages import Toast
from openfesl.web.sessions import check_form, checked_form, toast

SECTION = "settings"
# An icon file holds a few small images, so only this much of an upload is read into memory and
# a larger one is refused.
MAX_UPLOAD = 16 * MAX_ICON_SIZE


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


class IconStore(Protocol):
    """Where the settings page reads and changes the games' icons."""

    async def stored(self) -> dict[str, StoredIcon]:
        """Return each game's icon with when it was stored and by whom, by the game's id."""

    async def store(self, game: str, png: bytes, admin: Admin | None) -> None:
        """Store a game's icon in place of the one it had, and record the change.

        Args:
            game: The game's id.
            png: The icon as a PNG image.
            admin: The admin who stored it.
        """

    async def remove(self, game: str, admin: Admin | None) -> bool:
        """Remove a game's icon and record the change.

        Args:
            game: The game's id.
            admin: The admin who removed it.

        Returns:
            Whether the game had an icon.
        """


@dataclass(frozen=True, slots=True)
class IconView:
    """A game's icon as the settings page shows it.

    Attributes:
        width: Its width in pixels.
        height: Its height in pixels.
        kilobytes: Its size, in whole kilobytes rounded up.
        address: Its public address, which presence names.
        src: Its address on the page, which changes with the image so that a new one shows.
        at: When it was stored, if known.
        by: The display name of the admin who stored it; None for the command line.
    """

    width: int
    height: int
    kilobytes: int
    address: str
    src: str
    at: datetime | None
    by: str | None


@dataclass(frozen=True, slots=True)
class IconRefusal:
    """A file the settings page refused as a game's icon.

    Attributes:
        game: The game's id.
        problem: Why.
        name: The file's name as the browser sent it.
    """

    game: str
    problem: IconProblem
    name: str


@dataclass(frozen=True, slots=True)
class MarkView:
    """Whether a game shows its own server mark, as its block on the settings page shows it.

    Attributes:
        shown: Whether the game's official servers publish the mark.
        changed: The latest change as the admin's log records it; none while the default holds.
    """

    shown: bool
    changed: AuditEntry | None


@dataclass(frozen=True, slots=True)
class ZoneView:
    """The time zone of a game's rewards, as its block on the settings page shows it.

    Attributes:
        zone: The zone at whose midnight the days start.
        default: The zone of the game's data.
        changed: The latest change as the admin's log records it; none while the data's holds.
        entered: What the admin entered that names no zone, beside which the refusal stands;
            none when nothing was refused.
    """

    zone: str
    default: str
    changed: AuditEntry | None
    entered: str | None = None


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
    icons: IconStore,
    public_url: str | None,
    apply_actions: Callable[[], Awaitable[None]],
    marks: GameMarks,
    log: ActionHistory,
    rewards: RewardAdminViews,
) -> APIRouter:
    """Return the settings page: hosting, sign-in, the site's policy and each game's settings.

    Each game has its icon, its own server mark at official servers, the time zone of its
    rewards' days and its rules. Moderators see the settings; changing them needs the admin role.
    A change of the hosting mode asks first and applies to the games hosted now too, as does a
    game's own server mark without asking; policies and the zone apply from their next use, and
    an icon from presence's next update.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, each with its policies.
        policies: Reads the site's and each game's policies with the instance's overrides.
        store: Where the overrides are kept.
        hosting: Where the hosting mode is kept.
        servers: Counts the servers waiting for a decision.
        apply: Lists, hides or ends the servers' games as the hosting mode now says.
        provider: The admin panel's sign-in provider, if the deployment sets one up.
        icons: Where the games' icons are kept.
        public_url: The address the server's web pages are reached at, from which Discord
            fetches the icons; the request's own when None.
        apply_actions: Hands the admin's recorded actions to the running server, such as a
            changed icon to presence.
        marks: Says whether each game shows its own server mark at official servers, which the
            page changes.
        log: Says who last changed each game's own server mark and when.
        rewards: The games' rewards, whose zone the page changes.
    """
    router = APIRouter()

    async def page(
        request: Request,
        admin: Admin,
        status: int = 200,
        errors: dict[str, str] | None = None,
        refused: IconRefusal | None = None,
        entered: tuple[str, str] | None = None,
    ) -> HTMLResponse:
        mode = await hosting.hosting_mode()
        waiting = await servers.waiting(await servers.all())
        shown = [(game, policy_views(game, await policies.of(game))) for game in games]
        base = (public_url or str(request.base_url)).rstrip("/")
        stored = await icons.stored()
        site = await policies.site_confirmation()
        shown_marks = {
            game.id: MarkView(marks.shown(game), await log.latest(AdminAction.GAME_MARK, game.id))
            for game in games
            if game.official_attribute is not None
        }
        zones = {}
        for game in rewards.games:
            rules = await rewards.rewards.rules(game)
            assert rules is not None and game.rewards is not None
            zones[game.id] = ZoneView(
                rules.zone,
                game.rewards.zone,
                await log.latest(AdminAction.REWARD_ZONE, game.id),
                entered[1] if entered and entered[0] == game.id else None,
            )
        return await sessions.render(
            request,
            "admin.Settings",
            status,
            admin=admin,
            mode=mode.value,
            modes=[HostingMode.APPROVED.value, HostingMode.OPEN.value],
            waiting=len(waiting),
            games=shown,
            site=site,
            provider=provider,
            errors=errors or {},
            icons={game: _icon_view(base, game, icon) for game, icon in stored.items()},
            base=base,
            reachable=reaches_discord(public_url),
            replacing=request.query_params.get("icon", ""),
            refused=refused,
            largest=MAX_ICON_SIZE // (1024 * 1024),
            marks=shown_marks,
            zones=zones,
            failed=Toast("web.toast.failed", {}, error=True),
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
        form = await checked_form(request)
        chosen = str(form.get("mode", ""))
        back = localize("/admin/settings", request.state.language)
        if chosen not in set(HostingMode) or chosen == await hosting.hosting_mode():
            toast(request, "web.admin.toast.mode_unchanged")
            return RedirectResponse(back, 303)
        if form.get("confirmed") != "1":
            target = localize("/admin/settings/hosting", request.state.language)
            return RedirectResponse(f"{target}?{urlencode({'mode': chosen})}", 303)
        await hosting.set_hosting_mode(HostingMode(chosen), admin)
        await apply()
        toast(request, f"web.admin.toast.mode.{chosen}")
        return RedirectResponse(back, 303)

    @router.post("/admin/settings/site")
    async def change_site(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        on = form.get("accounts.email_verification") == "on"
        if on == await policies.site_confirmation():
            toast(request, "web.admin.toast.site_unchanged")
        else:
            await policies.set_site_confirmation(on, admin)
            toast(request, "web.admin.toast.site")
        return RedirectResponse(_settings_at(request, "site"), 303)

    @router.post("/admin/settings/games/{game_id}")
    async def change_policies(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = game_among(games, game_id)
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
            toast(request, "web.admin.toast.policies", game=game.name)
        else:
            toast(request, "web.admin.toast.policies_unchanged", game=game.name)
        return RedirectResponse(_settings_at(request, f"game-{game.id}"), 303)

    @router.post("/admin/settings/games/{game_id}/mark")
    async def change_mark(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = games.by_id(game_id)
        if game is None or game.official_attribute is None:
            raise HTTPException(404)
        shown = form.get("shown") == "on"
        state = "on" if shown else "off"
        name = translate(f"games.{game.id}.short", request.state.language)
        if shown == marks.shown(game):
            toast(request, f"web.admin.toast.game_mark.unchanged.{state}", game=name)
        else:
            views = await servers.all()
            official = sum(
                view.server.official and view.hosting is not None and view.game is game
                for view in views
            )
            await marks.show(game, shown, official, admin)
            toast(request, f"web.admin.toast.game_mark.{state}", game=name)
        return RedirectResponse(_settings_at(request, f"mark-{game.id}"), 303)

    @router.post("/admin/settings/games/{game_id}/zone")
    async def change_zone(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = rewards.game(game_id)
        rules = await rewards.rewards.rules(game)
        assert rules is not None and game.rewards is not None
        zone = str(form.get("zone", "")).strip()
        try:
            checked_zone(zone)
        except ValueError:
            return await page(request, admin, 400, entered=(game.id, zone))
        if zone == rules.zone:
            toast(request, "web.admin.toast.reward_zone.unchanged", zone=zone)
        else:
            value = None if zone == game.rewards.zone else zone
            details = {"before": rules.zone, "after": zone}
            await rewards.change(game, {"zone": value}, AdminAction.REWARD_ZONE, details, admin)
            toast(request, "web.admin.toast.reward_zone", zone=zone)
        return RedirectResponse(_settings_at(request, f"zone-{game.id}"), 303)

    @router.post("/admin/settings/games/{game_id}/icon")
    async def store_icon(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        async with request.form() as form:
            check_form(request, form)
            game = game_among(games, game_id)
            upload = form.get("icon")
            name, data = "", b""
            if isinstance(upload, UploadFile):
                name, data = upload.filename or "", await upload.read(MAX_UPLOAD + 1)
            try:
                if len(data) > MAX_UPLOAD:
                    raise IconRefused(IconProblem.SIZE)
                png = icon_png(data)
            except IconRefused as refusal:
                refused = IconRefusal(game.id, refusal.problem, name)
                return await page(request, admin, 400, refused=refused)
        await icons.store(game.id, png, admin)
        await apply_actions()
        toast(request, "web.admin.toast.icon.stored", game=game.name)
        return RedirectResponse(_settings_at(request, f"presence-{game.id}"), 303)

    @router.post("/admin/settings/games/{game_id}/icon/remove")
    async def remove_icon(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        await checked_form(request)
        game = game_among(games, game_id)
        if await icons.remove(game.id, admin):
            await apply_actions()
            toast(request, "web.admin.toast.icon.removed", game=game.name)
        else:
            toast(request, "web.admin.toast.icon.none", game=game.name)
        return RedirectResponse(_settings_at(request, f"presence-{game.id}"), 303)

    return router


def _settings_at(request: Request, part: str) -> str:
    return f"{localize('/admin/settings', request.state.language)}#{part}"


def _icon_view(base: str, game_id: str, icon: StoredIcon) -> IconView:
    width, height = png_size(icon.png)
    path = ICON_PATH.format(game=game_id)
    return IconView(
        width,
        height,
        math.ceil(len(icon.png) / 1024),
        base + path,
        f"{path}?v={icon_version(icon.png)}",
        icon.at,
        icon.by,
    )


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
