from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.password_resets import PATH, PasswordResets, PasswordSet, ResetGone
from openfesl.core.problems import address_problem
from openfesl.core.texts import translate
from openfesl.web.account.fields import field_errors
from openfesl.web.account.message import SECRET, message_page
from openfesl.web.account.session import open_page
from openfesl.web.account.sign_in import password_games
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, notify, take_notice
from openfesl.web.turnstile import Turnstile

RESET = "/password-reset"
_SIGN_IN = ("/sign-in", "web.account.sign_in")
_AGAIN = (RESET, "web.account.reset.again")
_DONE = "reset_done"


class ResetPages:
    """The game password reset: the request for a link, and the page the link opens."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        resets: Callable[[], PasswordResets],
        mail: bool,
        turnstile: Turnstile | None,
        services: bool,
    ) -> None:
        """Prepare the reset's pages.

        Args:
            pages: Renders the pages.
            games: The games, which the pages name where the game password is used.
            resets: Returns the resets, once the application has started.
            mail: Whether the server sends mail, without which nothing can be reset.
            turnstile: Cloudflare's check of the request; None when the deployment sets none up.
            services: Whether the deployment configures sign-in services, through which an
                account without its mailbox still signs in.
        """
        self._pages = pages
        self._games = games
        self._resets = resets
        self._mail = mail
        self._turnstile = turnstile
        self._services = services

    def routes(self) -> APIRouter:
        """Return the reset's pages and their forms."""
        router = APIRouter()

        @router.get(RESET, response_class=HTMLResponse)
        async def page(request: Request) -> Response:
            if request.session.get("notice", {}).get("kind") == _DONE:
                done = take_notice(request) or {}
                key = "web.account.reset.done"
                rows = ("web.account.reset.done.unlinked",) if done.get("unlinked") else ()
                return message_page(self._pages, request, 200, key, _SIGN_IN, rows)
            if not self._mail:
                return self._unavailable(request)
            return self._request_form(request)

        @router.post(RESET)
        async def ask(request: Request) -> Response:
            form = await request.form()
            address = str(form.get("email", "")).strip()
            if not self._mail:
                return self._unavailable(request)
            if not form_matches(request, form):
                return self._request_form(request, address, notice="form")
            if self._turnstile and not await self._turnstile.passed(request, form):
                return self._request_form(request, address, notice="check")
            if problem := address_problem(address):
                error = field_errors([problem], request.state.language)["email"]
                return self._request_form(request, address, error=error)
            await self._resets().request(address, request.state.language)
            key = "web.account.reset.requested"
            rows = ("web.account.reset.requested.again",)
            return message_page(self._pages, request, 200, key, _SIGN_IN, rows, address=address)

        @router.get(PATH, response_class=HTMLResponse)
        async def opened(request: Request, token: str) -> Response:
            account = await self._resets().opened(token)
            if isinstance(account, ResetGone):
                return self._gone(request, account)
            return self._password_form(request, account)

        @router.post(PATH)
        async def reset(request: Request, token: str) -> Response:
            form = await request.form()
            resets = self._resets()
            if not form_matches(request, form):
                account = await resets.opened(token)
                if isinstance(account, ResetGone):
                    return self._gone(request, account)
                return self._password_form(request, account, notice="form")
            done = await resets.reset(token, str(form.get("password", "")))
            if isinstance(done, ResetGone):
                return self._gone(request, done)
            if isinstance(done, PasswordSet):
                # The page that says so comes next, without the sessions the new password ended.
                notify(request, _DONE, unlinked="1" if done.unlinked else "")
                return open_page(request, RESET)
            account = await resets.opened(token)
            assert isinstance(account, Account)
            error = field_errors([done], request.state.language)["password"]
            return self._password_form(request, account, error=error)

        return router

    def _request_form(
        self, request: Request, address: str = "", error: str = "", notice: str = ""
    ) -> HTMLResponse:
        language = request.state.language
        page = self._pages.render(
            request,
            "account.Reset",
            400 if error or notice else 200,
            steel=True,
            csrf=form_token(request),
            address=address,
            error=error,
            notice=notice,
            check=self._turnstile.site_key if self._turnstile else "",
            services=self._services,
            password_games=password_games(self._games, language),
            personas=self._personas(language),
        )
        if self._turnstile:
            self._turnstile.allow(page)
        return page

    def _password_form(
        self, request: Request, account: Account, error: str = "", notice: str = ""
    ) -> HTMLResponse:
        return self._pages.render(
            request,
            "account.NewPassword",
            400 if error or notice else 200,
            steel=True,
            switch=False,
            headers=SECRET,
            csrf=form_token(request),
            account=account,
            error=error,
            notice=notice,
            personas=self._personas(request.state.language),
        )

    def _gone(self, request: Request, gone: ResetGone) -> HTMLResponse:
        key = "web.account.reset." + ("expired" if gone.expired else "unknown")
        status = 410 if gone.expired else 404
        return message_page(self._pages, request, status, key, _AGAIN, headers=SECRET)

    def _unavailable(self, request: Request) -> HTMLResponse:
        rows = ("web.account.reset.unavailable.services",) if self._services else ()
        key = "web.account.reset.unavailable"
        return message_page(self._pages, request, 200, key, _SIGN_IN, rows)

    def _personas(self, language: str) -> list[str]:
        return [translate(f"games.{game.id}.personas", language) for game in self._games]
