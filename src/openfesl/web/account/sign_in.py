from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.games import Games
from openfesl.core.policies import GamePolicies
from openfesl.core.sign_in import Refusal, SignInRefused, WebSignIn
from openfesl.core.texts import localize, translate
from openfesl.core.verification import VALID_FOR
from openfesl.web.account.session import end, signed_in, start
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, notify, take_notice

# Every page of the site, in any language, which a sign-in may return to.
SITE = ("/",)

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
    ) -> None:
        """Prepare the sign-in pages.

        Args:
            pages: Renders the pages.
            games: The games, which the page names where it explains accounts.
            policies: The site's policies, which decide whether registration needs mail.
            sign_in: Returns the sign-in checks, once the application has started.
            mail: Whether the server sends mail, which registration and resets need.
        """
        self._pages = pages
        self._games = games
        self._policies = policies
        self._sign_in = sign_in
        self._mail = mail

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
                return _again(request, back, "form")
            address = str(form.get("email", "")).strip()
            try:
                account = await self._sign_in().with_password(
                    address, str(form.get("password", ""))
                )
            except SignInRefused as refused:
                notice = _refusal_notice(refused)
                status = _STATUS[refused.refusal]
                return await self._render(request, back, notice, address, status)
            start(request, account, way="")
            return RedirectResponse(back or _account(request), 303)

        @router.post("/sign-out")
        async def sign_out(request: Request) -> Response:
            if not form_matches(request, await request.form()):
                return _again(request, "", "form")
            end(request)
            notify(request, "signed_out")
            return RedirectResponse(localize("/sign-in", request.state.language), 303)

        return router

    async def _render(
        self,
        request: Request,
        back: str,
        notice: dict[str, Any] | None,
        address: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        language = request.state.language
        registration = self._mail or not await self._policies.site_confirmation()
        return self._pages.render(
            request,
            "account.SignIn",
            status,
            steel=True,
            csrf=form_token(request),
            next=back,
            notice=notice,
            address=address,
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


def _refusal_notice(refused: SignInRefused) -> dict[str, Any]:
    account = refused.account
    match refused.refusal:
        case Refusal.UNCONFIRMED if account and account.verification_expires_at:
            sent = account.verification_expires_at - VALID_FOR
            return {"kind": "unconfirmed", "address": account.nuid, "sent": sent}
        case Refusal.DEACTIVATED if account:
            return {"kind": "deactivated", "since": account.state_changed}
        case Refusal.PAUSED:
            return {"kind": "paused"}
    return {"kind": "wrong"}


def _again(request: Request, back: str, kind: str) -> Response:
    notify(request, kind)
    target = localize("/sign-in", request.state.language)
    return RedirectResponse(f"{target}?{urlencode({'next': back})}" if back else target, 303)


def _account(request: Request) -> str:
    return localize("/account", request.state.language)
