from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.sign_in import ProofTarget, Refusal, SignInRefused, WebSignIn
from openfesl.core.texts import translate
from openfesl.web.account.services import PROOF, SignInServices
from openfesl.web.account.session import (
    PENDING,
    open_page,
    pending_identity,
    proved,
    refused_notice,
    shown,
    to_sign_in,
)
from openfesl.web.account.sign_in import password_games
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, take_notice
from openfesl.web.turnstile import Turnstile

_STATUS = {Refusal.WRONG: 400, Refusal.PAUSED: 429, Refusal.DEACTIVATED: 403}


class ProofPages:
    """The proof that links a service's new identity to the account its verified address has."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        services: SignInServices,
        sign_in: Callable[[], WebSignIn],
        turnstile: Turnstile | None,
    ) -> None:
        """Prepare the proof's page.

        Args:
            pages: Renders the pages.
            games: The games, which the page names where the game password is used.
            services: The sign-in services, through which a linked one proves the account.
            sign_in: Returns the sign-in checks, once the application has started.
            turnstile: Cloudflare's check of the forms that take a password; None when the
                deployment sets none up.
        """
        self._pages = pages
        self._games = games
        self._services = services
        self._sign_in = sign_in
        self._turnstile = turnstile

    def routes(self) -> APIRouter:
        """Return the proof's page and its forms."""
        router = APIRouter()

        @router.get(PROOF, response_class=HTMLResponse)
        async def page(request: Request) -> Response:
            target = await self._target(request)
            if target is None:
                return open_page(request, "/sign-in")
            return self._render(request, target, take_notice(request))

        @router.post(PROOF)
        async def with_password(request: Request) -> Response:
            form = await request.form()
            target = await self._target(request)
            if not form_matches(request, form) or target is None:
                return to_sign_in(request, "", "form")
            if self._turnstile and not await self._turnstile.passed(request, form):
                return self._render(request, target, {"kind": "check"}, 400)
            sign_in = self._sign_in()
            try:
                account = await sign_in.with_password(
                    target.account.nuid, str(form.get("password", ""))
                )
            except SignInRefused as refused:
                if refused.refusal is Refusal.UNCONFIRMED:
                    request.session.pop(PENDING, None)
                    return to_sign_in(request, "", **refused_notice(request, refused))
                notice = refused_notice(request, refused)
                return self._render(request, target, notice, _STATUS[refused.refusal])
            return await self._link(request, account)

        @router.post(PROOF + "/service")
        async def through_service(request: Request) -> Response:
            form = await request.form()
            target = await self._target(request)
            service_id = str(form.get("service", ""))
            # Only a configured service proves the account; its other identities are no way in.
            offered = self._services.view(service_id, request.state.language) is not None
            linked = {identity.service for identity in target.linked} if target else set()
            if not form_matches(request, form) or not offered or service_id not in linked:
                return to_sign_in(request, "", "form")
            return await self._services.start(request, service_id, "", proof=True)

        @router.post(PROOF + "/cancel")
        async def cancel(request: Request) -> Response:
            if form_matches(request, await request.form()):
                request.session.pop(PENDING, None)
            return open_page(request, "/sign-in")

        return router

    async def _target(self, request: Request) -> ProofTarget | None:
        pending = pending_identity(request)
        target = await self._sign_in().proof_target(pending[1]) if pending else None
        if target is None:
            request.session.pop(PENDING, None)
        return target

    async def _link(self, request: Request, account: Account) -> Response:
        pending = pending_identity(request)
        assert pending is not None
        identity, _ = pending
        linked = await self._sign_in().link(account, identity)
        service = self._services.view(identity.service, request.state.language)
        return proved(request, account, identity, service.name if service else "", linked)

    def _render(
        self,
        request: Request,
        target: ProofTarget,
        notice: Mapping[str, Any] | None,
        status: int = 200,
    ) -> HTMLResponse:
        language = request.state.language
        pending = pending_identity(request)
        assert pending is not None
        identity, _ = pending
        services = self._services
        linked = [
            view for other in target.linked if (view := services.view(other.service, language))
        ]
        page = self._pages.render(
            request,
            "account.Proof",
            status,
            steel=True,
            csrf=form_token(request),
            notice=shown(notice),
            service=services.view(identity.service, language),
            identity=identity,
            account=target.account,
            password=target.password,
            linked=linked,
            check=self._turnstile.site_key if self._turnstile else "",
            password_games=password_games(self._games, language),
            personas=[translate(f"games.{game.id}.personas", language) for game in self._games],
        )
        if self._turnstile:
            self._turnstile.allow(page)
        services.allow_forms(page)
        return page
