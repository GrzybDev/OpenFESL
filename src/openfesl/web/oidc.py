from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx2
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Request
from fastapi.responses import Response
from joserfc.errors import JoseError

from openfesl.web.security import HEADERS


class SignInFailed(Exception):
    """Raised for a sign-in through a provider that cannot start or did not complete."""


class OidcClient:
    """Signs people in through an OpenID Connect provider, with the code flow and PKCE."""

    def __init__(
        self,
        name: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        scope: str,
        callback: str,
        public_url: str | None,
    ) -> None:
        """Register the client with the provider that the issuer's discovery document describes.

        Args:
            name: The client's name, which keys its sign-ins' state in the session.
            issuer: The provider's issuer, whose discovery document names the endpoints.
            client_id: The client's id at the provider.
            client_secret: The client's secret at the provider.
            scope: The scopes it asks for, separated by spaces, such as openid profile.
            callback: The path the provider sends the browser back to.
            public_url: The address the pages are reached at; the request's own when None.
        """
        parts = urlsplit(issuer)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._callback = callback
        self._public_url = public_url.rstrip("/") if public_url else None
        self._app: Any = OAuth().register(
            name,
            client_id=client_id,
            client_secret=client_secret,
            client_kwargs={"code_challenge_method": "S256", "scope": scope},
            server_metadata_url=f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        )

    def allow_forms(self, response: Response) -> None:
        """Widen a page's policy so that its forms, such as sign-in, may lead to the provider.

        Args:
            response: The page.
        """
        policy = response.headers.get("Content-Security-Policy", HEADERS["Content-Security-Policy"])
        response.headers["Content-Security-Policy"] = policy.replace(
            "form-action 'self'", f"form-action 'self' {self._origin}"
        )

    async def start(self, request: Request) -> Response:
        """Return the redirect that sends the browser to the provider to sign in.

        Args:
            request: The request that starts the sign-in.

        Raises:
            SignInFailed: When the provider's discovery document cannot be read.
        """
        base = self._public_url or str(request.base_url).rstrip("/")
        try:
            redirect: Response = await self._app.authorize_redirect(request, base + self._callback)
        except (OAuthError, httpx2.HTTPError, ValueError) as error:
            raise SignInFailed(error) from error
        return redirect

    async def finish(self, request: Request) -> Mapping[str, Any]:
        """Finish the sign-in that the browser returns from, and return its ID token's claims.

        Args:
            request: The request to the callback.

        Raises:
            SignInFailed: When the sign-in was cancelled or its token is not valid.
        """
        try:
            token = await self._app.authorize_access_token(request)
            claims: Mapping[str, Any] = token["userinfo"]
        except (OAuthError, JoseError, httpx2.HTTPError, KeyError, ValueError) as error:
            # Such as a cancelled sign-in, or a token the provider's keys did not sign.
            raise SignInFailed(error) from error
        return claims
