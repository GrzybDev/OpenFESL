import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin, Role
from openfesl.core.games import Game, Games
from openfesl.core.referrals import Recruit, ReferralStore
from openfesl.core.rewards import Reward, Rewards
from openfesl.core.texts import languages
from openfesl.web.admin.auth import AdminSessions, back_to, game_among
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
from openfesl.web.referral_texts import NUMBERS, Chip, chip
from openfesl.web.reward_texts import RewardTexts
from openfesl.web.sessions import checked_form, toast


@dataclass(frozen=True, slots=True)
class RecruitLine:
    """A recruit as its recruiter's page in the panel lists it.

    Attributes:
        account_id: The recruit's account.
        name: Its display name, else its login name.
        joined: When it signed up, in words.
        chips: Where each step stands.
    """

    account_id: int
    name: str
    joined: str
    chips: list[Chip]


@dataclass(frozen=True, slots=True)
class AccountReferral:
    """Recruit a Friend of a game as an account's page in the panel shows it.

    Attributes:
        game: The game.
        recruiter: Who recruited the account by name and account, and when; none for an
            account that no link brought.
        recruits: The account's recruits, every one of them.
    """

    game: Game
    recruiter: tuple[str, int, str] | None
    recruits: list[RecruitLine]


class ReferralAdminViews:
    """Puts together what the admin panel shows of the games' Recruit a Friend, and changes it."""

    def __init__(
        self, rewards: RewardAdminViews, store: ReferralStore, engine: Rewards, games: Games
    ) -> None:
        """Read Recruit a Friend through the games' rewards.

        Args:
            rewards: The panel's views of the games' rewards, whose settings it changes.
            store: Where the recruits are kept.
            engine: Gives the rewards, which say where each recruit's steps stand.
            games: The games, of which those with referral steps have Recruit a Friend.
        """
        self.rewards = rewards
        self.store = store
        self._engine = engine
        self._games = [game for game in games if game.rewards and game.rewards.referral]

    def game(self, game_id: str) -> Game:
        """Return a game with Recruit a Friend by its id.

        Args:
            game_id: The game's id.

        Raises:
            HTTPException: 404 when no game with Recruit a Friend has the id.
        """
        return game_among(self._games, game_id)

    async def of_account(self, account_id: int, language: str) -> list[AccountReferral]:
        """Return who recruited an account and its recruits, in each game with Recruit a Friend.

        Args:
            account_id: The account.
            language: The page's language.
        """
        found = []
        for game in self._games:
            words = await self.rewards.words(game, language)
            mine = await self.store.recruited(game, account_id)
            recruiter = None
            if mine:
                name = mine.recruiter.display_name or mine.recruiter.nuid
                recruiter = (name, mine.recruiter.id, words.day(mine.joined, "yMMMd"))
            grouped: dict[int, tuple[Recruit, list[Reward]]] = {}
            for reward in await self._engine.recruiting(game, account_id):
                assert reward.referral is not None
                recruit = reward.referral.recruit
                grouped.setdefault(recruit.account.id, (recruit, []))[1].append(reward)
            recruits = [
                RecruitLine(
                    recruit.account.id,
                    recruit.account.display_name or recruit.account.nuid,
                    words.day(recruit.joined, "yMMMd"),
                    [chip(words, reward) for reward in rewards],
                )
                for recruit, rewards in grouped.values()
            ]
            if recruiter or recruits:
                found.append(AccountReferral(game, recruiter, recruits))
        return found

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

    Switching it and removing a recruit ask for a note as pages of their own, which open as
    dialogs where scripts run; a step's editor saves its goal, window and both gifts with a note,
    and moderators read it. Each change is recorded with its note, and applies from the next page
    or claim.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and change Recruit a Friend.
    """
    router = APIRouter()
    rewards = views.rewards
    switch = "/admin/rewards/{game_id}/referral/switch"
    remove = "/admin/accounts/{recruit_id:int}/referral/{game_id}/remove"

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
        form = await checked_form(request)
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
            codes=languages(),
        )

    @router.post("/admin/rewards/{game_id}/referral/{step}")
    async def save_step(request: Request, game_id: str, step: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
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

    async def remove_page(
        request: Request, admin: Admin, game: Game, recruit: Recruit, back: str, error: str = ""
    ) -> HTMLResponse:
        return await sessions.render(
            request,
            "admin.ReferralRemove",
            400 if error else 200,
            admin=admin,
            of=game,
            recruit=recruit,
            back=back,
            error=error,
        )

    @router.get(remove, response_class=HTMLResponse)
    async def ask_remove(
        request: Request, recruit_id: int, game_id: str, back: str = ""
    ) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, "accounts")
        game = views.game(game_id)
        recruit = await views.store.recruited(game, recruit_id)
        if recruit is None:
            raise HTTPException(404)
        return await remove_page(request, admin, game, recruit, back_to(request, back))

    @router.post(remove)
    async def remove_recruit(request: Request, recruit_id: int, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, "accounts")
        form = await checked_form(request)
        game = views.game(game_id)
        back = back_to(request, str(form.get("back", "")))
        note = str(form.get("note", "")).strip()
        recruit = await views.store.recruited(game, recruit_id)
        if recruit is None:
            # Removed meanwhile, as from another tab.
            return RedirectResponse(back, 303)
        if not note:
            return await remove_page(request, admin, game, recruit, back, "note")
        if removed := await views.store.remove(game, recruit_id, note, admin):
            names = [a.display_name or a.nuid for a in (removed.account, removed.recruiter)]
            toast(request, "web.admin.toast.referral.removed", recruit=names[0], name=names[1])
        return RedirectResponse(back, 303)

    return router
