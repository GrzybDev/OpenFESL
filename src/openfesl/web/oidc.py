from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import httpx2
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Request
from fastapi.responses import Response
from joserfc.errors import JoseError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from openfesl.web.security import allow


class SignInFailed(Exception):
    """Raised for a sign-in through a service that cannot start or did not complete."""


class ServiceKind(StrEnum):
    """How a sign-in service tells who signed in."""

    OIDC = "oidc"
    """OpenID Connect: its discovery document names the endpoints, its ID token the account."""
    OAUTH2 = "oauth2"
    """Plain OAuth2: the settings name the endpoints, and a userinfo URL answers the account."""


class SignInService(BaseModel):
    """A service that people sign in through, as the deployment configures it.

    Attributes:
        id: The service's id in forms and the database, such as discord; it never changes.
        name: Its name on the pages, such as Discord.
        kind: How it tells who signed in.
        glyph: The neutral glyph that its tile shows, never a brand's logo.
        client_id: The site's client id there.
        client_secret: The site's client secret there.
        scope: The scopes it asks for, separated by spaces, as few as the sign-in needs.
        issuer: An OpenID Connect service's issuer, whose discovery document names the
            endpoints.
        authorize_url: A plain OAuth2 service's authorization endpoint.
        token_url: A plain OAuth2 service's token endpoint.
        userinfo_url: A plain OAuth2 service's endpoint that answers the signed-in account.
        emails_url: An endpoint that lists the account's addresses with whether each is
            verified, for services whose userinfo leaves that out.
        subject_claim: The claim that holds the account's id there, which never changes.
        name_claim: The claim that holds the account's name there.
        email_claim: The claim that holds its e-mail address.
        verified_claim: The claim that says whether the service verified that address.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")
    name: str
    kind: ServiceKind = ServiceKind.OIDC
    glyph: Literal["chat", "code", "globe", "key"] = "key"
    client_id: str
    client_secret: SecretStr
    scope: str
    issuer: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    emails_url: str | None = None
    subject_claim: str = "sub"
    name_claim: str = "preferred_username"
    email_claim: str = "email"
    verified_claim: str = "email_verified"

    @model_validator(mode="after")
    def _check_endpoints(self) -> Self:
        if self.kind is ServiceKind.OIDC and not self.issuer:
            raise ValueError("an OpenID Connect service needs its issuer")
        if self.kind is ServiceKind.OAUTH2 and not (
            self.authorize_url and self.token_url and self.userinfo_url
        ):
            raise ValueError("a plain OAuth2 service needs its three endpoints")
        return self

    @property
    def origin(self) -> str:
        """The origin of the page that the browser signs in on, such as https://github.com."""
        parts = urlsplit(f"{self.issuer if self.kind is ServiceKind.OIDC else self.authorize_url}")
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def host(self) -> str:
        """The host of the page that the browser signs in on, such as github.com."""
        return urlsplit(self.origin).hostname or ""


@dataclass(frozen=True, slots=True)
class ServiceAnswer:
    """Who signed in through a service.

    Attributes:
        subject: The account's id at the service, which never changes.
        name: The account's name there.
        address: The e-mail address the service verified for the account; None when it
            verified none.
        claims: Everything the service said about the account, such as an admin's groups.
    """

    subject: str
    name: str
    address: str | None
    claims: Mapping[str, Any]


class ServiceClient:
    """Signs people in through a service, with the code flow and PKCE."""

    def __init__(
        self, service: SignInService, name: str, callback: str, public_url: str | None
    ) -> None:
        """Register the client with the service.

        Args:
            service: The service.
            name: The client's name, which keys its sign-ins' state in the session.
            callback: The path the service sends the browser back to.
            public_url: The address the pages are reached at; the request's own when None.
        """
        self.service = service
        self._callback = callback
        self._public_url = public_url.rstrip("/") if public_url else None
        endpoints: dict[str, Any] = {
            "authorize_url": service.authorize_url,
            "access_token_url": service.token_url,
        }
        if service.kind is ServiceKind.OIDC:
            discovery = f"{service.issuer}".rstrip("/") + "/.well-known/openid-configuration"
            endpoints = {"server_metadata_url": discovery}
        self._app: Any = OAuth().register(
            name,
            client_id=service.client_id,
            client_secret=service.client_secret.get_secret_value(),
            client_kwargs={"code_challenge_method": "S256", "scope": service.scope},
            **endpoints,
        )

    def allow_forms(self, response: Response) -> None:
        """Widen a page's policy so that its forms, such as sign-in, may lead to the service.

        Args:
            response: The page.
        """
        allow(response, "form-action", self.service.origin)

    async def start(self, request: Request) -> Response:
        """Return the redirect that sends the browser to the service to sign in.

        Args:
            request: The request that starts the sign-in.

        Raises:
            SignInFailed: When the service's discovery document cannot be read.
        """
        base = self._public_url or str(request.base_url).rstrip("/")
        try:
            redirect: Response = await self._app.authorize_redirect(request, base + self._callback)
        except (OAuthError, httpx2.HTTPError, ValueError) as error:
            raise SignInFailed(error) from error
        return redirect

    async def finish(self, request: Request) -> ServiceAnswer:
        """Finish the sign-in that the browser returns from, and return who signed in.

        Args:
            request: The request to the callback.

        Raises:
            SignInFailed: When the sign-in was cancelled, its token is not valid or the service
                did not say who signed in.
        """
        service = self.service
        try:
            token = await self._app.authorize_access_token(request)
            if service.kind is ServiceKind.OIDC:
                claims: Mapping[str, Any] = token["userinfo"]
            else:
                claims = await self._read(service.userinfo_url, token)
            subject = str(claims[service.subject_claim])
            address = await self._verified_address(claims, token)
        except (OAuthError, JoseError, httpx2.HTTPError, KeyError, TypeError, ValueError) as error:
            # Such as a cancelled sign-in, a token the service's keys did not sign or an answer
            # without the account.
            raise SignInFailed(error) from error
        name = claims.get(service.name_claim) or claims.get("name") or subject
        return ServiceAnswer(subject, str(name), address, claims)

    async def _verified_address(
        self, claims: Mapping[str, Any], token: Mapping[str, Any]
    ) -> str | None:
        service = self.service
        if service.emails_url:
            listed = await self._read(service.emails_url, token)
            verified = [entry for entry in listed if _verified(entry.get("verified"))]
            # The account's primary address first, as the service shows it.
            verified.sort(key=lambda entry: not entry.get("primary"))
            return str(verified[0]["email"]) if verified else None
        address = claims.get(service.email_claim)
        return str(address) if address and _verified(claims.get(service.verified_claim)) else None

    async def _read(self, url: str | None, token: Mapping[str, Any]) -> Any:
        answer = await self._app.get(url, token=token)
        answer.raise_for_status()
        return answer.json()


def _verified(value: object) -> bool:
    # Some services write the flag as text.
    return value is True or value == "true"
