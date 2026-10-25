import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx2
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from joserfc.errors import JoseError

from openfesl.core.admins import Admin, GroupRoles, Role
from openfesl.core.texts import localize
from openfesl.web.pages import Pages, Toast, Viewer
from openfesl.web.security import HEADERS

logger = logging.getLogger(__name__)

# A signed-in admin signs in again after this long, as a stolen cookie should not last.
SESSION_LIFETIME = timedelta(hours=12)


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


class FormExpired(Exception):
    """Raised for an admin form whose token does not match the session's; nothing changes."""


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
        self, pages: Pages, provider: SignInProvider | None, public_url: str | None
    ) -> None:
        """Prepare the sign-in through the provider.

        Args:
            pages: Renders the pages.
            provider: The sign-in provider; None when the deployment sets up none.
            public_url: The address the pages are reached at, to which the provider sends the
                admin back; the request's own when None.
        """
        self._pages = pages
        self.provider = provider
        self._public_url = public_url.rstrip("/") if public_url else None
        self._sign_in_policy = HEADERS["Content-Security-Policy"]
        self._client: Any = None
        if provider is not None:
            # The sign-in form leads to the provider, which the policy must let a form go to.
            parts = urlsplit(provider.issuer)
            self._sign_in_policy = self._sign_in_policy.replace(
                "form-action 'self'", f"form-action 'self' {parts.scheme}://{parts.netloc}"
            )
            issuer = provider.issuer.rstrip("/")
            self._client = OAuth().register(
                "admin",
                client_id=provider.client_id,
                client_secret=provider.client_secret,
                client_kwargs={"code_challenge_method": "S256", "scope": "openid profile"},
                server_metadata_url=f"{issuer}/.well-known/openid-configuration",
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
            request.session["notice"] = {"kind": "expired"}
            raise SignInNeeded(here)
        admin = _admin(signed_in)
        if role is Role.ADMIN and admin.role is not Role.ADMIN:
            raise NeedsRole(admin, section)
        return admin

    def check(self, request: Request, form: Mapping[str, Any]) -> None:
        """Check that a form came from a page of this session.

        Args:
            request: The form's request.
            form: The form's fields.

        Raises:
            FormExpired: When its token is not the session's.
        """
        token = request.session.get("csrf")
        sent = form.get("csrf")
        if not token or not isinstance(sent, str) or not secrets.compare_digest(token, sent):
            raise FormExpired

    def render(
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
        token = request.session.setdefault("csrf", secrets.token_urlsafe(32))
        viewer = None
        if admin is not None:
            shown = request.session.pop("toast", None)
            toast = Toast(shown["key"], shown["values"], shown["error"]) if shown else None
            viewer = Viewer(admin, self.provider.name if self.provider else "", toast)
        return self._pages.render(
            request, template, status, steel=True, viewer=viewer, csrf=token, **context
        )

    def toast(self, request: Request, key: str, error: bool = False, **values: str) -> None:
        """Have the next admin page say what came of a change.

        Args:
            request: The change's request.
            key: The text in the catalogues.
            error: Whether the change failed.
            values: The text's placeholders.
        """
        request.session["toast"] = {"key": key, "values": values, "error": error}

    def routes(self) -> APIRouter:
        """Return the sign-in and sign-out pages."""
        router = APIRouter()

        @router.get("/admin/sign-in", response_class=HTMLResponse)
        async def sign_in_page(request: Request, next: str = "") -> HTMLResponse:
            notice = request.session.pop("notice", None)
            page = self.render(
                request, "admin.SignIn", next=_inside(next), notice=notice, provider=self.provider
            )
            page.headers["Content-Security-Policy"] = self._sign_in_policy
            return page

        @router.post("/admin/sign-in")
        async def start(request: Request) -> Response:
            form = await request.form()
            self.check(request, form)
            if self._client is None:
                return RedirectResponse(localize("/admin/sign-in", request.state.language), 303)
            request.session["after"] = {
                "next": _inside(str(form.get("next", ""))),
                "language": request.state.language,
            }
            callback = (
                f"{self._public_url}/admin/sign-in/callback"
                if self._public_url
                else str(request.url_for("sign_in_callback"))
            )
            try:
                redirect: Response = await self._client.authorize_redirect(request, callback)
            except (OAuthError, httpx2.HTTPError, ValueError) as error:
                logger.warning("The admin sign-in provider's discovery failed: %s", error)
                request.session["notice"] = {"kind": "unreachable"}
                return RedirectResponse(localize("/admin/sign-in", request.state.language), 303)
            return redirect

        @router.get("/admin/sign-in/callback", name="sign_in_callback")
        async def callback(request: Request) -> Response:
            after = request.session.pop("after", None) or {"next": "", "language": "en"}
            failed = RedirectResponse(localize("/admin/sign-in", after["language"]), 303)
            if self.provider is None:
                return failed
            try:
                token = await self._client.authorize_access_token(request)
                subject, name, groups = _identity(token)
            except (OAuthError, JoseError, httpx2.HTTPError, KeyError, ValueError) as error:
                # Such as a cancelled sign-in, or a token the provider's keys did not sign.
                logger.warning("An admin sign-in did not complete: %r", error)
                request.session["notice"] = {"kind": "failed"}
                return failed
            role = self.provider.roles.role(groups)
            if role is None:
                request.session["notice"] = {"kind": "refused", "name": name}
                return failed
            request.session["admin"] = {
                "subject": subject,
                "name": name,
                "role": role.value,
                "since": datetime.now(UTC).isoformat(),
            }
            # A new session gets a new token, so that a token seen before signing in is no use.
            request.session["csrf"] = secrets.token_urlsafe(32)
            return RedirectResponse(after["next"] or localize("/admin/", after["language"]), 303)

        @router.post("/admin/sign-out")
        async def sign_out(request: Request) -> Response:
            self.check(request, await request.form())
            request.session.pop("admin", None)
            request.session["notice"] = {"kind": "signed_out"}
            request.session["csrf"] = secrets.token_urlsafe(32)
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
        return self.render(request, "admin.Refused", 403, admin, reason="expired", section="")

    async def needs_role(self, request: Request, error: Exception) -> Response:
        """Answer a page that the admin's role does not open with the page that says so.

        Args:
            request: The request.
            error: The NeedsRole raised.
        """
        assert isinstance(error, NeedsRole)
        return self.render(
            request, "admin.Refused", 403, error.admin, reason="role", section=error.section
        )


def _admin(signed_in: Mapping[str, str]) -> Admin:
    return Admin(signed_in["subject"], signed_in["name"], Role(signed_in["role"]))


def _inside(path: str) -> str:
    # Only the panel's own pages open after signing in, never another site.
    if path.startswith(("/admin/", "/pl/admin/")) and "//" not in path and "\\" not in path:
        return path
    return ""


def _identity(token: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    claims = token["userinfo"]
    name = claims.get("preferred_username") or claims.get("name") or claims["sub"]
    groups = claims.get("groups") or []
    return str(claims["sub"]), str(name), [str(group) for group in groups]
