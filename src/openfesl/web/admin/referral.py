import json
from collections.abc import Mapping
from dataclasses import replace

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.games import Game, Games
from openfesl.core.texts import languages
from openfesl.web.admin.auth import AdminSessions, back_to
from openfesl.web.admin.referral_editor import (
    SIDES,
    StepEditor,
    checked,
    drafted,
    from_form,
    step_written,
)
from openfesl.web.admin.referral_view import goal
from openfesl.web.admin.rewards import SECTION, RewardAdminViews, rewards_url
from openfesl.web.referral_texts import NUMBERS
from openfesl.web.reward_texts import RewardTexts
from openfesl.web.sessions import check_form, toast


class ReferralAdminViews:
    """Puts together what the admin panel shows of the games' Recruit a Friend, and changes it."""

    def __init__(self, rewards: RewardAdminViews, games: Games) -> None:
        """Read Recruit a Friend through the games' rewards.

        Args:
            rewards: The panel's views of the games' rewards, whose settings it changes.
            games: The games, of which those with referral steps have Recruit a Friend.
        """
        self.rewards = rewards
        self._games = [game for game in games if game.rewards and game.rewards.referral]

    def game(self, game_id: str) -> Game:
        """Return a game with Recruit a Friend by its id.

        Args:
            game_id: The game's id.

        Raises:
            HTTPException: 404 when no game with Recruit a Friend has the id.
        """
        game = next((game for game in self._games if game.id == game_id), None)
        if game is None:
            raise HTTPException(404)
        return game

    def editor(
        self,
        words: RewardTexts,
        name: str,
        written: StepEditor | None,
        searched: Mapping[str, str] | None = None,
    ) -> StepEditor:
        """Return a step's editor, with its fields as saved or as a form sent them.

        Each side's search lists the catalogue's items that a step may give, with the side's
        chosen item first.

        Args:
            words: Writes the page's language, with the game's rewards as the instance sets them.
            name: The step's name.
            written: The fields, and refusals of a refused save; none for the step as saved.
            searched: Each side's search and chosen item, as the live search sends them over the
                step as saved.
        """
        rules = words.rules.referral
        assert rules is not None
        step = rules.steps[name]
        other = "second" if name == "first" else "first"
        order = words.texts.text(
            f"web.admin.rewards.referral.order.{name}",
            n=NUMBERS[other],
            level=rules.steps[other].level,
        )
        imported = {}
        for side in SIDES:
            text_id = getattr(step, side).name
            if found := words.catalogue.text(text_id):
                imported[side] = (found, text_id)
        shown = written or StepEditor(
            name,
            NUMBERS[name],
            str(step.level),
            str(step.days or ""),
            {side: drafted(getattr(step, side)) for side in SIDES},
        )
        catalogue, heroes = words.catalogue, words.game.heroes
        granted = heroes.granted if heroes else frozenset()
        sides, candidates = dict(shown.sides), {}
        for side, draft in sides.items():
            if searched is not None:
                sides[side] = draft = replace(
                    draft,
                    query=searched.get(f"{side}-q", ""),
                    item=searched.get(f"{side}-item", ""),
                )
            listed = catalogue.givable(draft.query, granted)
            number = draft.item
            chosen = catalogue.item(int(number) if number.isascii() and number.isdecimal() else 0)
            if chosen and chosen not in [item for item, _ in listed]:
                listed.insert(0, (chosen, catalogue.name(chosen.id) or str(chosen.id)))
            candidates[side] = listed
        return replace(
            shown,
            sides=sides,
            imported=imported,
            order=order,
            points=words.rules.points is not None,
            candidates=candidates,
            searchable=catalogue.imported,
            icons=catalogue.contents.icons > 0,
        )


def referral_routes(sessions: AdminSessions, views: ReferralAdminViews) -> APIRouter:
    """Return the changes of the games' Recruit a Friend, which need the admin role.

    Switching it asks for a note as a page of its own, which opens as a dialog where scripts run;
    a step's editor saves its goal, window and both gifts with a note, and moderators read it.
    Each change is recorded with its note, and applies from the next page or claim.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and change Recruit a Friend.
    """
    router = APIRouter()
    rewards = views.rewards
    switch = "/admin/rewards/{game_id}/referral/switch"

    async def switch_page(
        request: Request, admin: Admin, game: Game, back: str, error: str = ""
    ) -> HTMLResponse:
        rules = await rewards.rewards.rules(game)
        assert rules is not None and rules.referral is not None
        return await sessions.render(
            request,
            "admin.ReferralSwitch",
            400 if error else 200,
            admin=admin,
            of=game,
            running=rules.referral.enabled,
            back=back,
            error=error,
        )

    @router.get(switch, response_class=HTMLResponse)
    async def ask_switch(request: Request, game_id: str, back: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.game(game_id)
        back = back_to(request, back, rewards_url(request, game))
        return await switch_page(request, admin, game, back)

    @router.post(switch)
    async def switch_referral(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.game(game_id)
        back = back_to(request, str(form.get("back", "")), rewards_url(request, game))
        rules = await rewards.rewards.rules(game)
        assert rules and rules.referral and game.rewards and game.rewards.referral
        on = form.get("enabled") == "1"
        if on == rules.referral.enabled:
            # The form was drawn before someone else made the same change.
            return RedirectResponse(back, 303)
        note = str(form.get("note", "")).strip()
        if not note:
            return await switch_page(request, admin, game, back, "note")
        value = None if on == game.rewards.referral.enabled else "on" if on else "off"
        details = {"enabled": on, "note": note}
        action = AdminAction.REFERRAL_SETTINGS
        await rewards.change(game, {"referral.enabled": value}, action, details, admin)
        toast(request, f"web.admin.toast.referral.{'on' if on else 'off'}")
        return RedirectResponse(back, 303)

    @router.get("/admin/rewards/{game_id}/referral/{step}", response_class=HTMLResponse)
    async def edit_step(request: Request, game_id: str, step: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        if step not in NUMBERS:
            raise HTTPException(404)
        words = await rewards.words(game, request.state.language)
        return await sessions.render(
            request,
            "admin.ReferralStep",
            admin=admin,
            of=game,
            found=views.editor(words, step, None, request.query_params or None),
            manages=admin.role is Role.ADMIN,
            codes=languages(),
        )

    @router.post("/admin/rewards/{game_id}/referral/{step}")
    async def save_step(request: Request, game_id: str, step: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.game(game_id)
        if step not in NUMBERS:
            raise HTTPException(404)
        words = await rewards.words(game, request.state.language)
        rules, seed = words.rules.referral, game.rewards.referral if game.rewards else None
        assert rules is not None and seed is not None
        written = from_form(form, step)
        # Find lists a side's items without scripts, saving nothing.
        saved = {} if "find" in form else checked(words, seed.steps[step], step, written)
        if isinstance(saved, dict):
            found = views.editor(words, step, replace(written, refused=saved))
            status = 400 if saved else 200
            return await sessions.render(
                request,
                "admin.ReferralStep",
                status,
                admin=admin,
                of=game,
                found=found,
                manages=True,
                codes=languages(),
            )
        steps = {**rules.steps, step: saved}
        whole = {"enabled": seed.enabled} | {key: step_written(s) for key, s in steps.items()}
        unchanged = all(steps[key] == seed.steps[key] for key in steps)
        before, after = step_written(rules.steps[step]), step_written(saved)
        details = {"step": step, "before": before, "after": after, "note": written.note}
        settings = {"referral": None if unchanged else json.dumps(whole)}
        await rewards.change(game, settings, AdminAction.REFERRAL_SETTINGS, details, admin)
        toast(
            request,
            "web.admin.toast.referral.step",
            n=str(NUMBERS[step]),
            goal=goal(words, saved),
            recruiter=", ".join(words.texts.gift_words(words.catalogue, saved.recruiter.gift)),
            recruit=", ".join(words.texts.gift_words(words.catalogue, saved.recruit.gift)),
        )
        return RedirectResponse(rewards_url(request, game) + "#rewards-referral", 303)

    return router
