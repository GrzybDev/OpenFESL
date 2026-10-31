from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.calendar import today
from openfesl.core.games import Game, Games
from openfesl.core.reward_claims import RewardStore
from openfesl.core.texts import localize, translate
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.rewards import SECTION, RewardAdminViews, rewards_url
from openfesl.web.sessions import check_form, toast


def reward_change_routes(
    sessions: AdminSessions, views: RewardAdminViews, store: RewardStore
) -> APIRouter:
    """Return the changes to the games' rewards, which need the admin role.

    Pausing the claims, or letting them go on, asks for a note as a page of its own, which opens
    as a dialog over the rewards' page where scripts run.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and change the rewards.
        store: Records the days on which claims are paused, whose daily gifts wait.
    """
    router = APIRouter()
    path = "/admin/rewards/{game_id}/pause"

    async def pause_page(
        request: Request, admin: Admin, game: Game, back: str, error: str = ""
    ) -> HTMLResponse:
        rules = await views.rewards.rules(game)
        assert rules is not None
        return await sessions.render(
            request,
            "admin.RewardPause",
            400 if error else 200,
            admin=admin,
            of=game,
            pausing=not rules.paused,
            back=back,
            error=error,
        )

    @router.get(path, response_class=HTMLResponse)
    async def ask(request: Request, game_id: str, back: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.game(game_id)
        return await pause_page(
            request, admin, game, back_to(request, back, rewards_url(request, game))
        )

    @router.post(path)
    async def pause(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.game(game_id)
        back = back_to(request, str(form.get("back", "")), rewards_url(request, game))
        note = str(form.get("note", "")).strip()
        pausing = form.get("paused") == "1"
        rules = await views.rewards.rules(game)
        assert rules is not None and game.rewards is not None
        if rules.paused == pausing:
            # The form was drawn before someone else made the same change.
            toast(request, f"web.admin.toast.rewards.{'paused' if pausing else 'resumed'}.already")
            return RedirectResponse(back, 303)
        if not note:
            return await pause_page(request, admin, game, back, "note")
        value = None if pausing == game.rewards.paused else "on" if pausing else "off"
        details = {"paused": pausing, "note": note}
        await views.change(game, {"paused": value}, AdminAction.REWARD_PAUSE, details, admin)
        if pausing:
            # Recorded at once, so that a pause shorter than the follower's check still keeps
            # its day's daily gifts.
            await store.pause(game.id, today(datetime.now(UTC), rules.zone))
        toast(request, f"web.admin.toast.rewards.{'paused' if pausing else 'resumed'}")
        return RedirectResponse(back, 303)

    return router


def reward_entry(
    action: AdminAction, target: str, details: dict[str, Any], games: Games, language: str
) -> tuple[str, str, str | None]:
    """Return how the audit log shows a change of a game's rewards.

    Args:
        action: What the admin did.
        target: The game's id.
        details: The change's details, as the admin's log keeps them.
        games: The games, whose rewards the change was made to.
        language: The log's language.

    Returns:
        The kind of the action, what it was done to in words and the panel's page of it.
    """
    game = games.by_id(target)
    name = game.name if game else target
    shown = translate("web.admin.rewards.subtitle", language) % {"game": name}
    href = localize(f"/admin/rewards/{target}", language) if game else None
    return f"{action.value}.{'on' if details.get('paused') else 'off'}", shown, href
