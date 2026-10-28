from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.display_names import DisplayNames
from openfesl.core.games import Games
from openfesl.core.problems import Problem
from openfesl.core.registration import Terms
from openfesl.core.texts import localize, translate
from openfesl.web.account.fields import field_errors
from openfesl.web.account.services import SignInServices
from openfesl.web.account.session import DISPLAY_NAME, SITE, answered, open_page, signed_in
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, notify, toast


class DisplayNamePages:
    """The step that names an account made in a game, and the account page's form that renames."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        accounts: Callable[[int], Awaitable[Account | None]],
        names: Callable[[], DisplayNames],
        terms: Terms,
        services: SignInServices,
    ) -> None:
        """Prepare the display name's page.

        Args:
            pages: Renders the page.
            games: The games, which the page names where the account was made and plays.
            accounts: Returns an account by its number.
            names: Returns the display names, once the application has started.
            terms: The terms, whose current version the page says the account accepted or not.
            services: The sign-in services, which the page names as what the account page links.
        """
        self._pages = pages
        self._games = games
        self._accounts = accounts
        self._names = names
        self._terms = terms
        self._services = services

    def routes(self) -> APIRouter:
        """Return the display name's page and the form that chooses or changes the name."""
        router = APIRouter()

        @router.get(DISPLAY_NAME, response_class=HTMLResponse)
        async def page(request: Request, next: str = "") -> Response:
            back = inside(next, SITE)
            account = await self._signed_in(request)
            if not isinstance(account, Account):
                return account
            if account.display_name is not None:
                return _done(request, back)
            return await self._render(request, account, back)

        @router.post(DISPLAY_NAME)
        async def choose(request: Request) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), SITE)
            account = await self._signed_in(request)
            if not isinstance(account, Account):
                return account
            name = str(form.get("display_name", "")).strip()
            if account.display_name is not None:
                return await self._rename(request, account, name, form_matches(request, form))
            if not form_matches(request, form):
                return await self._render(request, account, back, name, notice="form")
            if problem := await self._names().choose(account, name):
                return await self._render(request, account, back, name, problem)
            toast(request, "web.account.toast.named", name=name)
            return _done(request, back)

        return router

    async def _signed_in(self, request: Request) -> Account | Response:
        # The signed-in account, or the sign-in page for a visitor who is not signed in.
        player = signed_in(request)
        account = await self._accounts(player.account_id) if player else None
        if account is None:
            notify(request, "needed", page="display_name")
            return open_page(request, "/sign-in", localize(DISPLAY_NAME, request.state.language))
        return account

    async def _rename(self, request: Request, account: Account, name: str, sent: bool) -> Response:
        # The account page's form, which changes the display name of an account that has one.
        if not sent:
            return answered(request, "profile", form="display_name", value=name, notice="form")
        if problem := await self._names().choose(account, name):
            return answered(request, "profile", [problem], form="display_name", value=name)
        toast(request, "web.account.toast.named", name=name)
        return answered(request, "profile")

    async def _render(
        self,
        request: Request,
        account: Account,
        back: str,
        value: str | None = None,
        problem: Problem | None = None,
        notice: str = "",
    ) -> HTMLResponse:
        language = request.state.language
        names = self._names()
        played = await names.played(account)
        suggestion = await names.suggestion(played) if value is None else None
        # The games it plays, or every game for an account that plays none yet.
        games = [entry.game for entry in played] or list(self._games)
        return self._pages.render(
            request,
            "account.DisplayName",
            400 if problem or notice else 200,
            steel=True,
            csrf=form_token(request),
            next=back,
            account=account,
            made_in=self._games.by_id(account.made_in or ""),
            value=suggestion.name if suggestion else value or "",
            suggestion=suggestion,
            error=field_errors([problem], language).get("display_name", "") if problem else "",
            taken=problem is not None and problem.kind == "taken",
            notice=notice,
            played=played,
            personas=[translate(f"games.{game.id}.personas", language) for game in games],
            accepted=account.terms_version == self._terms.version,
            services=[service.name for service in self._services.views(language)],
        )


def _done(request: Request, back: str) -> RedirectResponse:
    return RedirectResponse(back or localize("/account", request.state.language), 303)
