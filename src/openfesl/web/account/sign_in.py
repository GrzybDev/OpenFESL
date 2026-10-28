from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.games import Games
from openfesl.core.limits import Wait
from openfesl.core.policies import GamePolicies
from openfesl.core.sign_in import LinkSent, Refusal, SignInRefused, WebSignIn
from openfesl.core.texts import localize, translate
from openfesl.web.account.services import SignInServices
from openfesl.web.account.session import (
    SITE,
    WAITING,
    end,
    refused_notice,
    signed_in,
    start,
    to_sign_in,
)
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, take_notice
from openfesl.web.turnstile import Turnstile

# The notices' times, which the session keeps as ISO text.
_TIMES = ("sent", "since", "expires", "last", "until")

_STATUS = {
    Refusal.WRONG: 400,
    Refusal.PAUSED: 429,
    Refusal.UNCONFIRMED: 403,
    Refusal.DEACTIVATED: 403,
}


class SignInPages:
    """The site's sign-in and sign-out, which sign accounts in on the site's pages."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        policies: GamePolicies,
        sign_in: Callable[[], WebSignIn],
        mail: bool,
        turnstile: Turnstile | None,
        services: SignInServices,
    ) -> None:
        """Prepare the sign-in pages.

        Args:
            pages: Renders the pages.
            games: The games, which the page names where it explains accounts.
            policies: The site's policies, which decide whether registration needs mail.
            sign_in: Returns the sign-in checks, once the application has started.
            mail: Whether the server sends mail, which registration and resets need.
            turnstile: Cloudflare's check of the forms that take an address; None when the
                deployment sets none up.
            services: The sign-in services, whose tiles lead the page.
        """
        self._pages = pages
        self._games = games
        self._policies = policies
        self._sign_in = sign_in
        self._mail = mail
        self._turnstile = turnstile
        self._services = services

    def routes(self) -> APIRouter:
        """Return the sign-in page and the forms that sign in and out."""
        router = APIRouter()

        @router.get("/sign-in", response_class=HTMLResponse)
        async def page(request: Request, next: str = "") -> Response:
            back = inside(next, SITE)
            if signed_in(request):
                return RedirectResponse(back or _account(request), 303)
            return await self._render(request, back, take_notice(request))

        @router.post("/sign-in")
        async def with_password(request: Request) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), SITE)
            if not form_matches(request, form):
                return to_sign_in(request, back, "form")
            address = str(form.get("email", "")).strip()
            if self._turnstile and not await self._turnstile.passed(request, form):
                return await self._render(request, back, {"kind": "check"}, address, 400)
            try:
                account = await self._sign_in().with_password(
                    address, str(form.get("password", ""))
                )
            except SignInRefused as refused:
                notice = refused_notice(request, refused)
                status = _STATUS[refused.refusal]
                return await self._render(request, back, notice, address, status)
            request.session.pop(WAITING, None)
            start(request, account, way="")
            return RedirectResponse(back or _account(request), 303)

        @router.post("/sign-in/confirmation")
        async def confirmation(request: Request) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), SITE)
            account_id = request.session.get(WAITING)
            if not form_matches(request, form) or account_id is None:
                return to_sign_in(request, back, "form")
            sign_in = self._sign_in()
            if self._turnstile and not await self._turnstile.passed(request, form):
                account = await sign_in.waiting(account_id)
                if account is None:
                    return to_sign_in(request, back, "form")
                refused = SignInRefused(Refusal.UNCONFIRMED, account)
                notice = {**refused_notice(request, refused), "check": "1"}
                return await self._render(request, back, notice, status=400)
            match await sign_in.send_confirmation(account_id, request.state.language):
                case LinkSent(address, expires):
                    sent = {"kind": "link_sent", "address": address, "expires": expires}
                    return await self._render(request, back, sent)
                case Wait(last, until):
                    wait = {"kind": "link_wait", "last": last, "until": until}
                    return await self._render(request, back, wait, status=429)
            request.session.pop(WAITING, None)
            return RedirectResponse(localize("/sign-in", request.state.language), 303)

        @router.post("/sign-out")
        async def sign_out(request: Request) -> Response:
            if not form_matches(request, await request.form()):
                return to_sign_in(request, "", "form")
            end(request)
            return to_sign_in(request, "", "signed_out")

        return router

    async def _render(
        self,
        request: Request,
        back: str,
        notice: Mapping[str, Any] | None,
        address: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        language = request.state.language
        if notice:
            notice = {key: _restored(key, value) for key, value in notice.items()}
        registration = self._mail or not await self._policies.site_confirmation()
        page = self._pages.render(
            request,
            "account.SignIn",
            status,
            steel=True,
            csrf=form_token(request),
            next=back,
            notice=notice,
            address=address,
            check=self._turnstile.site_key if self._turnstile else "",
            services=self._services.views(language),
            mail=self._mail,
            registration=registration,
            password_games=[
                translate(f"games.{game.id}.short", language)
                for game in self._games
                if not game.launched
            ],
            personas=[
                translate(f"games.{game.id}.personas.kept", language) for game in self._games
            ],
        )
        if self._turnstile:
            self._turnstile.allow(page)
        self._services.allow_forms(page)
        return page


def _restored(key: str, value: Any) -> Any:
    return datetime.fromisoformat(value) if key in _TIMES and isinstance(value, str) else value


def _account(request: Request) -> str:
    return localize("/account", request.state.language)
