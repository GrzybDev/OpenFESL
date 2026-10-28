import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import SecretStr

from openfesl.core.admins import Admin, GroupRoles, Role
from openfesl.core.texts import localize
from openfesl.web.oidc import ServiceClient, SignInFailed, SignInService
from openfesl.web.pages import Pages, Viewer
from openfesl.web.sessions import (
    check_form,
    form_token,
    inside,
    notify,
    renew_form_token,
    take_notice,
    take_toast,
)

logger = logging.getLogger(__name__)

# A signed-in admin signs in again after this long, as a stolen cookie should not last.
SESSION_LIFETIME = timedelta(hours=12)
# The panel's pages in each language, the only ones that a sign-in or a form returns to.
PANEL = ("/admin/", "/pl/admin/")


@dataclass(frozen=True, slots=True)
class SignInProvider:
    """The OpenID Connect provider that admins sign in to the admin panel through.

    Attributes:
        name: Its name on the sign-in button.
        issuer: Its issuer, whose discovery document names the endpoints.
        client_id: The panel's client id there.
        client_secret: The panel's client secret there.
        roles: Its groups whose members get a role.
    """

    name: str
    issuer: str
    client_id: str
    client_secret: str
    roles: GroupRoles


class SignInNeeded(Exception):
    """Raised for an admin page opened without a valid session; the sign-in page follows."""

    def __init__(self, path: str) -> None:
        """Remember the page to open after signing in.

        Args:
            path: The page's address.
        """
        self.path = path


class NeedsRole(Exception):
    """Raised for an admin page that the signed-in admin's role does not open."""

    def __init__(self, admin: Admin, section: str) -> None:
        """Say who was refused where.

        Args:
            admin: The signed-in admin.
            section: The panel's section, such as providers.
        """
        self.admin = admin
        self.section = section


class AdminSessions:
    """Signs admins in through the deployment's provider and keeps their sessions."""

    def __init__(
        self,
        pages: Pages,
        provider: SignInProvider | None,
        public_url: str | None,
        menu_counts: Callable[[], Awaitable[Mapping[str, int]]],
    ) -> None:
        """Prepare the sign-in through the provider.

        Args:
            pages: Renders the pages.
            provider: The sign-in provider; None when the deployment sets up none.
            public_url: The address the pages are reached at, to which the provider sends the
                admin back; the request's own when None.
            menu_counts: Returns the numbers the menu shows by its items, such as the servers
                waiting for a decision.
        """
        self._pages = pages
        self._menu_counts = menu_counts
        self.provider = provider
        self._client = (
            ServiceClient(
                SignInService(
                    id="admin",
                    name=provider.name,
                    issuer=provider.issuer,
                    client_id=provider.client_id,
                    client_secret=SecretStr(provider.client_secret),
                    scope="openid profile",
                ),
                "admin",
                callback="/admin/sign-in/callback",
                public_url=public_url,
            )
            if provider
            else None
        )

    def admin(self, request: Request, role: Role | None = None, section: str = "") -> Admin:
        """Return the signed-in admin of a request to an admin page.

        Args:
            request: The request.
            role: The role the page needs; any role opens it when None.
            section: The panel's section, named on the page that refuses a role.

        Raises:
            SignInNeeded: When nobody is signed in or the session expired.
            NeedsRole: When the admin's role does not open the page.
        """
        signed_in = request.session.get("admin")
        here = localize(request.url.path, request.state.language)
        if signed_in is None:
            raise SignInNeeded(here)
        if datetime.now(UTC) - datetime.fromisoformat(signed_in["since"]) > SESSION_LIFETIME:
            request.session.pop("admin")
            notify(request, "expired")
            raise SignInNeeded(here)
        admin = _admin(signed_in)
        if role is Role.ADMIN and admin.role is not Role.ADMIN:
            raise NeedsRole(admin, section)
        return admin

    async def render(
        self,
        request: Request,
        template: str,
        status: int = 200,
        admin: Admin | None = None,
        **context: Any,
    ) -> HTMLResponse:
        """Render an admin page, with the token its forms send back.

        Args:
            request: The request the page answers.
            template: The page's component, such as admin.Home.
            status: The response's status code.
            admin: The signed-in admin; None on the sign-in pages.
            context: The page's own values.
        """
        token = form_token(request)
        viewer = None
        if admin is not None:
            through = self.provider.name if self.provider else ""
            viewer = Viewer(admin, through, take_toast(request), await self._menu_counts())
        return self._pages.render(
            request, template, status, steel=True, viewer=viewer, csrf=token, **context
        )

    def routes(self) -> APIRouter:
        """Return the sign-in and sign-out pages."""
        router = APIRouter()

        @router.get("/admin/sign-in", response_class=HTMLResponse)
        async def sign_in_page(request: Request, next: str = "") -> HTMLResponse:
            page = await self.render(
                request,
                "admin.SignIn",
                next=inside(next, PANEL),
                notice=take_notice(request),
                provider=self.provider,
            )
            if self._client is not None:
                self._client.allow_forms(page)
            return page

        @router.post("/admin/sign-in")
        async def start(request: Request) -> Response:
            form = await request.form()
            check_form(request, form)
            if self._client is None:
                return RedirectResponse(localize("/admin/sign-in", request.state.language), 303)
            request.session["after"] = {
                "next": inside(str(form.get("next", "")), PANEL),
                "language": request.state.language,
            }
            try:
                return await self._client.start(request)
            except SignInFailed as error:
                logger.warning("The admin sign-in provider's discovery failed: %s", error)
                notify(request, "unreachable")
                return RedirectResponse(localize("/admin/sign-in", request.state.language), 303)

        @router.get("/admin/sign-in/callback")
        async def callback(request: Request) -> Response:
            after = request.session.pop("after", None) or {"next": "", "language": "en"}
            failed = RedirectResponse(localize("/admin/sign-in", after["language"]), 303)
            if self.provider is None or self._client is None:
                return failed
            try:
                answer = await self._client.finish(request)
            except SignInFailed as error:
                logger.warning("An admin sign-in did not complete: %r", error)
                notify(request, "failed")
                return failed
            subject, name = answer.subject, answer.name
            role = self.provider.roles.role(
                [str(group) for group in answer.claims.get("groups", [])]
            )
            if role is None:
                notify(request, "refused", name=name)
                return failed
            request.session["admin"] = {
                "subject": subject,
                "name": name,
                "role": role.value,
                "since": datetime.now(UTC).isoformat(),
            }
            renew_form_token(request)
            return RedirectResponse(after["next"] or localize("/admin/", after["language"]), 303)

        @router.post("/admin/sign-out")
        async def sign_out(request: Request) -> Response:
            check_form(request, await request.form())
            request.session.pop("admin", None)
            notify(request, "signed_out")
            renew_form_token(request)
            return RedirectResponse(localize("/admin/sign-in", request.state.language), 303)

        return router

    async def sign_in_needed(self, request: Request, error: Exception) -> Response:
        """Answer an admin page opened without a session with the way to sign in.

        Args:
            request: The request.
            error: The SignInNeeded raised, naming the page.
        """
        assert isinstance(error, SignInNeeded)
        target = localize("/admin/sign-in", request.state.language)
        return RedirectResponse(f"{target}?{urlencode({'next': error.path})}", 303)

    async def form_expired(self, request: Request, error: Exception) -> Response:
        """Answer a form whose token does not match with the page that says so.

        Args:
            request: The form's request.
            error: The FormExpired raised.
        """
        signed_in = request.session.get("admin")
        admin = _admin(signed_in) if signed_in else None
        return await self.render(request, "admin.Refused", 403, admin, reason="expired", section="")

    async def needs_role(self, request: Request, error: Exception) -> Response:
        """Answer a page that the admin's role does not open with the page that says so.

        Args:
            request: The request.
            error: The NeedsRole raised.
        """
        assert isinstance(error, NeedsRole)
        return await self.render(
            request, "admin.Refused", 403, error.admin, reason="role", section=error.section
        )


def _admin(signed_in: Mapping[str, str]) -> Admin:
    return Admin(signed_in["subject"], signed_in["name"], Role(signed_in["role"]))


def back_to(request: Request, back: str, default: str = "/admin/") -> str:
    """Return the panel's page that a form returns to: the one it names, or a default.

    Args:
        request: The form's request, whose language the default takes.
        back: The page the form names; another site's is never used.
        default: The page otherwise, such as the panel's home.
    """
    return inside(back, PANEL) or localize(default, request.state.language)
