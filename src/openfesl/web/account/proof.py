from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.limits import Wait
from openfesl.core.proofs import PATH, ProofFound, ProofGone, ProofMail
from openfesl.core.sign_in import ProofTarget, Refusal, SignInRefused, WebSignIn
from openfesl.core.texts import localize, translate
from openfesl.web.account.message import SECRET
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
        proof_mail: Callable[[], ProofMail],
        mail: bool,
        turnstile: Turnstile | None,
    ) -> None:
        """Prepare the proof's page.

        Args:
            pages: Renders the pages.
            games: The games, which the page names where the game password is used.
            services: The sign-in services, through which a linked one proves the account.
            sign_in: Returns the sign-in checks, once the application has started.
            proof_mail: Returns the proofs by e-mail, once the application has started.
            mail: Whether the server sends mail, which the proof by e-mail and resets need.
            turnstile: Cloudflare's check of the forms that take a password or send mail; None
                when the deployment sets none up.
        """
        self._pages = pages
        self._games = games
        self._services = services
        self._sign_in = sign_in
        self._proof_mail = proof_mail
        self._mail = mail
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

        @router.post(PROOF + "/mail")
        async def by_mail(request: Request) -> Response:
            form = await request.form()
            target = await self._target(request)
            pending = pending_identity(request)
            if not form_matches(request, form) or target is None or pending is None:
                return to_sign_in(request, "", "form")
            if not self._mail:
                return open_page(request, PROOF)
            if self._turnstile and not await self._turnstile.passed(request, form):
                return self._render(request, target, {"kind": "check_mail"}, 400)
            identity, _ = pending
            service = self._services.view(identity.service, request.state.language)
            name = service.name if service else identity.service
            sent = await self._proof_mail().send(
                target.account, identity, name, request.state.language
            )
            if isinstance(sent, Wait):
                wait = {"kind": "link_wait", "last": sent.last, "until": sent.until}
                return self._render(request, target, wait, 429)
            return self._render(request, target, {"kind": "link_sent", "expires": sent.expires})

        @router.post(PROOF + "/cancel")
        async def cancel(request: Request) -> Response:
            if form_matches(request, await request.form()):
                request.session.pop(PENDING, None)
            return open_page(request, "/sign-in")

        # Opening the link only asks, as mail scanners open links too; its button links.
        @router.get(PATH, response_class=HTMLResponse)
        async def open_link(request: Request, token: str) -> Response:
            return self._link_page(request, token, await self._proof_mail().find(token))

        # After the proof's own forms, which the token's pattern would match too.
        @router.post(PATH)
        async def use_link(request: Request, token: str) -> Response:
            proof_mail = self._proof_mail()
            if not form_matches(request, await request.form()):
                return self._link_page(request, token, await proof_mail.find(token), "form")
            try:
                used = await proof_mail.use(token)
            except SignInRefused as refused:
                return to_sign_in(request, "", **refused_notice(request, refused))
            if isinstance(used, ProofGone):
                return self._link_page(request, token, used)
            view = self._services.view(used.identity.service, request.state.language)
            name = view.name if view else used.identity.service
            return proved(request, used.account, used.identity, name, used.linked)

        return router

    def _link_page(
        self, request: Request, token: str, found: ProofFound | ProofGone, notice: str = ""
    ) -> HTMLResponse:
        language = request.state.language
        if isinstance(found, ProofGone):
            view = self._services.view(found.service or "", language)
            service = view.name if view and found.service else ""
            status, name, account = 404 if found.service is None else 410, "", ""
        else:
            view = self._services.view(found.identity.service, language)
            service = view.name if view else found.identity.service
            status = 400 if notice else 200
            name = found.identity.name
            account = found.account.display_name or found.account.nuid
        return self._pages.render(
            request,
            "account.ProofLink",
            status,
            steel=True,
            switch=False,
            headers=SECRET,
            csrf=form_token(request),
            action=localize(PATH.format(token=token), language),
            service=service,
            name=name,
            account=account,
            notice=notice,
        )

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
            mail=self._mail,
            check=self._turnstile.site_key if self._turnstile else "",
            password_games=password_games(self._games, language),
            personas=[translate(f"games.{game.id}.personas", language) for game in self._games],
        )
        if self._turnstile:
            self._turnstile.allow(page)
        services.allow_forms(page)
        return page
