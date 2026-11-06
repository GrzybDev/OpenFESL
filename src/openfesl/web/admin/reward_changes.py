from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.calendar import today
from openfesl.core.games import Game, Games
from openfesl.core.reward_claims import RewardStore
from openfesl.core.reward_rules import RewardRules
from openfesl.core.texts import localize, translate
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.reward_editor import event_entry
from openfesl.web.admin.rewards import SECTION, RewardAdminViews, rewards_page, rewards_url
from openfesl.web.pages import number, whole_number
from openfesl.web.sessions import checked_form, toast


def reward_change_routes(
    sessions: AdminSessions, views: RewardAdminViews, store: RewardStore
) -> APIRouter:
    """Return the changes to the games' rewards, which need the admin role.

    Pausing the claims, or letting them go on, asks for a note as a page of its own, which opens
    as a dialog over the rewards' page where scripts run. Switches of events, their parts, the
    starter packs and the first chest's key flip at once, and the milestones' Funds and switches
    are saved together. Changes apply from the next claim; nothing granted is taken back.

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
        form = await checked_form(request)
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

    @router.post("/admin/rewards/{game_id}/switch")
    async def switch(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = views.game(game_id)
        rules = await views.rewards.rules(game)
        assert rules is not None and game.rewards is not None
        key = str(form.get("path", ""))
        now, seed = _enabled(rules, key), _enabled(game.rewards, key)
        if now is None or seed is None:
            raise HTTPException(404)
        on = bool(form.get("enabled"))
        if on != now:
            value = None if on == seed else "on" if on else "off"
            details = {"switch": key, "enabled": on}
            await views.change(
                game, {f"{key}.enabled": value}, AdminAction.REWARD_SWITCH, details, admin
            )
        name = switch_name(game, key, request.state.language)
        toast(request, f"web.admin.toast.rewards.switched.{'on' if on else 'off'}", name=name)
        back = back_to(request, str(form.get("back", "")), rewards_url(request, game))
        return RedirectResponse(back, 303)

    @router.post("/admin/rewards/{game_id}/milestones")
    async def milestones(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        fields = {name: str(value).strip() for name, value in form.items()}
        game = views.game(game_id)
        rules = await views.rewards.rules(game)
        assert rules is not None and game.rewards is not None
        language = request.state.language
        changes = []
        for key, milestone in rules.milestones.items():
            text = fields.get(f"funds-{key}", "")
            funds = whole_number(text, MOST_BALANCE)
            if funds is None:
                why = translate("web.admin.rewards.refused.funds", language) % {
                    "milestone": switch_name(game, f"milestones.{key}", language),
                    "currency": translate(f"games.{game.id}.currency.{rules.currency}", language),
                    "most": number(MOST_BALANCE, language),
                }
                refused = fields | {"refused": key}
                return await rewards_page(sessions, views, request, admin, game, refused, why)
            before = {"funds": milestone.gift.funds, "enabled": milestone.enabled}
            after = {"funds": funds, "enabled": bool(fields.get(f"on-{key}"))}
            if after != before:
                changes.append((key, before, after))
        for key, before, after in changes:
            seed = game.rewards.milestones[key]
            settings: dict[str, str | None] = {}
            if after["funds"] != before["funds"]:
                funds = after["funds"]
                settings[f"milestones.{key}.gift.funds"] = (
                    None if funds == seed.gift.funds else str(funds)
                )
            if after["enabled"] != before["enabled"]:
                on = after["enabled"]
                settings[f"milestones.{key}.enabled"] = (
                    None if on == seed.enabled else "on" if on else "off"
                )
            details = {"milestone": key, "before": before, "after": after}
            await views.change(game, settings, AdminAction.REWARD_MILESTONE, details, admin)
        toast(request, "web.admin.toast.rewards.milestones")
        return RedirectResponse(rewards_url(request, game) + "#rewards-milestones", 303)

    return router


def reward_entry(
    action: AdminAction, target: str, details: dict[str, Any], games: Games, language: str
) -> tuple[str, str, str | None, str]:
    """Return how the audit log shows a change of a game's rewards.

    Args:
        action: What the admin did: paused or resumed the claims, flipped a switch, changed a
            milestone, an event or the zone of the days.
        target: The game's id.
        details: The change's details, as the admin's log keeps them.
        games: The games, whose rewards the change was made to.
        language: The log's language.

    Returns:
        The kind of the action, what it was done to in words, the panel's page of it and what
        changed in words.
    """
    game = games.by_id(target)
    if game is None or game.rewards is None:
        return action.value, target, None, ""
    href = localize(f"/admin/rewards/{target}", language)
    if action is AdminAction.REWARD_PAUSE:
        shown = translate("web.admin.rewards.subtitle", language) % {"game": game.name}
        return f"{action.value}.{'on' if details['paused'] else 'off'}", shown, href, ""
    if action is AdminAction.REWARD_SWITCH:
        shown = switch_name(game, details["switch"], language)
        return f"{action.value}.{'on' if details['enabled'] else 'off'}", shown, href, ""
    if action is AdminAction.REWARD_ZONE:
        shown = translate("web.admin.rewards.subtitle", language) % {"game": game.name}
        zones = translate("web.admin.audit.reward_zone", language) % details
        return action.value, shown, localize(f"/admin/settings#zone-{target}", language), zones
    if action is AdminAction.REWARD_EVENT:
        return action.value, *event_entry(game, details, language)
    before, after = details["before"], details["after"]
    said = []
    if before["funds"] != after["funds"]:
        currency = translate(f"games.{game.id}.currency.{game.rewards.currency}", language)
        said.append(
            translate("web.admin.audit.reward_funds", language)
            % {
                "before": number(before["funds"], language),
                "after": number(after["funds"], language),
                "currency": currency,
            }
        )
    if before["enabled"] != after["enabled"]:
        state = "on" if after["enabled"] else "off"
        said.append(translate(f"web.admin.audit.reward_switch.{state}", language))
    shown = switch_name(game, f"milestones.{details['milestone']}", language)
    return action.value, shown, f"{href}#rewards-milestones", "; ".join(said)


def switch_name(game: Game, key: str, language: str) -> str:
    """Return what a switch of a game's rewards switches, as the game's data names it.

    Args:
        game: The game, which gives rewards.
        key: The switch's path in the rewards, such as events.halloween.parts.royal.
        language: The language of the name.
    """
    rules = game.rewards
    assert rules is not None
    match key.split("."):
        case ["events", event] if event in rules.events:
            return translate(rules.events[event].title, language)
        case ["events", event, "parts", part] if part in getattr(
            rules.events.get(event), "parts", {}
        ):
            # A part is for a faction's heroes, or the account's own.
            faction = rules.events[event].parts[part].faction
            who = (
                f"games.{game.id}.faction.{faction}"
                if faction
                else "web.admin.rewards.part.account"
            )
            title = translate(rules.events[event].title, language)
            return f"{title} · {translate(who, language)}"
        case ["starter"]:
            return translate("web.admin.rewards.packs", language)
        case ["first_chest"]:
            return translate("web.admin.rewards.keys.first", language)
        case ["milestones", milestone] if milestone in rules.milestones:
            found = rules.milestones[milestone]
            return translate(found.name, language).format(goal=number(found.goal, language))
    return key


def _enabled(rules: RewardRules, key: str) -> bool | None:
    # Whether the part of the rewards that a switch's path names is switched on; none for a
    # path that names nothing with a switch.
    node: Any = rules.model_dump()
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    found = node.get("enabled") if isinstance(node, dict) else None
    return found if isinstance(found, bool) else None
