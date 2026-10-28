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
from openfesl.web.account.session import DISPLAY_NAME, SITE, open_page, signed_in
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, notify, toast


class DisplayNamePages:
    """The step where an account made in a game chooses its display name, once."""

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
        """Return the display name's page and its form."""
        router = APIRouter()

        @router.get(DISPLAY_NAME, response_class=HTMLResponse)
        async def page(request: Request, next: str = "") -> Response:
            back = inside(next, SITE)
            account = await self._unnamed(request)
            if not isinstance(account, Account):
                return account or _done(request, back)
            return await self._render(request, account, back)

        @router.post(DISPLAY_NAME)
        async def choose(request: Request) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), SITE)
            account = await self._unnamed(request)
            if not isinstance(account, Account):
                return account or _done(request, back)
            name = str(form.get("display_name", "")).strip()
            if not form_matches(request, form):
                return await self._render(request, account, back, name, notice="form")
            if problem := await self._names().choose(account, name):
                return await self._render(request, account, back, name, problem)
            toast(request, "web.account.toast.named", name=name)
            return _done(request, back)

        return router

    async def _unnamed(self, request: Request) -> Account | Response | None:
        # The signed-in account while it has no display name; the sign-in page for a visitor
        # who is not signed in, and None for an account that has one already.
        player = signed_in(request)
        account = await self._accounts(player.account_id) if player else None
        if account is None:
            notify(request, "needed", page="display_name")
            return open_page(request, "/sign-in", localize(DISPLAY_NAME, request.state.language))
        return account if account.display_name is None else None

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
