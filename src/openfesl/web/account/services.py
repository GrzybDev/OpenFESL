import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.identities import Identity
from openfesl.core.sign_in import ProofNeeded, Refusal, SignInRefused, WebSignIn
from openfesl.core.texts import localize, translate
from openfesl.web.account.session import (
    PENDING,
    SITE,
    WAITING,
    open_page,
    pending_identity,
    proved,
    refused_notice,
    start,
    to_sign_in,
)
from openfesl.web.oidc import ServiceAnswer, ServiceClient, SignInFailed, SignInService
from openfesl.web.sessions import form_matches, inside, notify

logger = logging.getLogger(__name__)

# The one address that every service sends the browser back to; the session names the service.
CALLBACK = "/sign-in/callback"
# The sign-in under way at a service: to sign in, or to prove an account for a pending identity.
FLOW = "service"
PROOF = "/sign-in/proof"


@dataclass(frozen=True, slots=True)
class ServiceView:
    """A sign-in service as its tile and the explanation of accounts show it.

    Attributes:
        id: The service's id, which its tile sends.
        name: Its name.
        glyph: Its neutral glyph.
        host: The host of the page the browser signs in on.
        scopes: What its scopes ask for, in the page's language.
    """

    id: str
    name: str
    glyph: str
    host: str
    scopes: list[str]


class SignInServices:
    """The sign-in services the deployment configures, and the sign-ins through them."""

    def __init__(
        self,
        services: list[SignInService],
        public_url: str | None,
        sign_in: Callable[[], WebSignIn],
    ) -> None:
        """Prepare a client for each service.

        Args:
            services: The services, in the order their tiles show.
            public_url: The address the pages are reached at, to which the services send the
                browser back; the request's own when None.
            sign_in: Returns the sign-in checks, once the application has started.
        """
        self._clients = {
            service.id: ServiceClient(service, f"sign-in-{service.id}", CALLBACK, public_url)
            for service in services
        }
        self._sign_in = sign_in

    def views(self, language: str) -> list[ServiceView]:
        """Return the services as the sign-in page shows them.

        Args:
            language: The page's language code.
        """
        return [
            ServiceView(
                service.id,
                service.name,
                service.glyph,
                service.host,
                [_scope(scope, language) for scope in service.scope.split()],
            )
            for service in (client.service for client in self._clients.values())
        ]

    def view(self, service_id: str, language: str) -> ServiceView | None:
        """Return a configured service as the pages show it.

        Args:
            service_id: The service's id.
            language: The page's language code.
        """
        return next((view for view in self.views(language) if view.id == service_id), None)

    async def start(
        self, request: Request, service_id: str, back: str, proof: bool = False
    ) -> Response:
        """Send the browser to a service to sign in.

        Args:
            request: The request of the form that starts it.
            service_id: The service's id.
            back: The page to open after signing in, if any.
            proof: Whether the sign-in proves the account of a pending identity rather than
                signing in.
        """
        client = self._clients[service_id]
        request.session[FLOW] = {
            "service": service_id,
            "next": back,
            "language": request.state.language,
            "proof": proof,
        }
        try:
            return await client.start(request)
        except SignInFailed as error:
            logger.warning("The sign-in service %s cannot be reached: %s", service_id, error)
            notify(request, "unreachable", service=client.service.name)
            return open_page(request, PROOF if proof else "/sign-in", back)

    def allow_forms(self, response: Response) -> None:
        """Widen a page's policy so that its service tiles may lead to every service.

        Args:
            response: The page.
        """
        for client in self._clients.values():
            client.allow_forms(response)

    def routes(self) -> APIRouter:
        """Return the form that starts a sign-in through a service and the page it returns to."""
        router = APIRouter()

        @router.post("/sign-in/service")
        async def begin(request: Request) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), SITE)
            service_id = str(form.get("service", ""))
            if not form_matches(request, form) or service_id not in self._clients:
                return to_sign_in(request, back, "form")
            return await self.start(request, service_id, back)

        @router.get(CALLBACK)
        async def finish(request: Request) -> Response:
            flow = request.session.pop(FLOW, None)
            client = self._clients.get(flow["service"]) if flow else None
            if flow is None or client is None:
                return RedirectResponse(localize("/sign-in", request.state.language), 303)
            request.state.language = flow["language"]
            service = client.service
            try:
                answer = await client.finish(request)
            except SignInFailed as error:
                logger.info("A sign-in through %s did not complete: %r", service.id, error)
                notify(request, "failed", service=service.name)
                return open_page(request, PROOF if flow["proof"] else "/sign-in", flow["next"])
            if flow["proof"]:
                return await self._proved(request, service, answer)
            return await self._signed_in(request, service, answer, flow["next"])

        return router

    async def _signed_in(
        self, request: Request, service: SignInService, answer: ServiceAnswer, back: str
    ) -> Response:
        identity = Identity(service.id, answer.subject, answer.name, answer.address)
        try:
            account = await self._sign_in().with_service(identity)
        except SignInRefused as refused:
            return to_sign_in(request, back, **refused_notice(request, refused, service.name))
        language = request.state.language
        if not isinstance(account, Account):
            request.session[PENDING] = {
                "service": service.id,
                "subject": identity.subject,
                "name": identity.name,
                "address": identity.address,
                "next": back,
                "account": account.account.id if isinstance(account, ProofNeeded) else None,
            }
            target = PROOF if isinstance(account, ProofNeeded) else "/register/service"
            return RedirectResponse(localize(target, language), 303)
        request.session.pop(WAITING, None)
        start(request, account, way=service.name)
        return RedirectResponse(back or localize("/account", language), 303)

    async def _proved(
        self, request: Request, service: SignInService, answer: ServiceAnswer
    ) -> Response:
        # The account of a linked service proves the pending identity's account.
        pending = pending_identity(request)
        if pending is None:
            return RedirectResponse(localize("/sign-in", request.state.language), 303)
        identity, account_id = pending
        sign_in = self._sign_in()
        try:
            found = await sign_in.with_service(
                Identity(service.id, answer.subject, answer.name, answer.address)
            )
        except SignInRefused as refused:
            if refused.refusal is Refusal.UNCONFIRMED or refused.account is None:
                request.session.pop(PENDING, None)
                return to_sign_in(request, "", **refused_notice(request, refused))
            since = refused.account.state_changed
            notify(request, "deactivated", since=since.isoformat() if since else "")
            return open_page(request, PROOF, "")
        if not isinstance(found, Account) or found.id != account_id:
            notify(request, "other", service=service.name)
            return open_page(request, PROOF, "")
        linked = await sign_in.link(found, identity)
        pending_client = self._clients.get(identity.service)
        name = pending_client.service.name if pending_client else identity.service
        return proved(request, found, identity, name, linked)


def _scope(scope: str, language: str) -> str:
    try:
        return translate(f"web.account.scope.{scope}", language)
    except KeyError:
        return scope
