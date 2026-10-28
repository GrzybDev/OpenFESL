"""A sign-in service for the tests, which signs in whom a test names: an OpenID Connect provider,
and a plain OAuth2 one at /oauth with a userinfo URL and a list of e-mail addresses."""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx2
import uvicorn
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route


@dataclass
class IdentityProvider:
    """The provider's address and the account its next sign-in returns."""

    url: str
    user: dict[str, Any] = field(default_factory=dict)
    # A sign-in the account cancels returns an error instead of a code.
    cancel: bool = False
    # A forged sign-in returns a token signed with a key the provider does not publish.
    forge: bool = False
    # What the plain OAuth2 service's list of the account's e-mail addresses holds.
    emails: list[dict[str, Any]] = field(default_factory=list)
    _codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    _tokens: dict[str, dict[str, Any]] = field(default_factory=dict)


@asynccontextmanager
async def identity_provider() -> AsyncIterator[IdentityProvider]:
    """Run the provider on a free port until the block ends."""
    key = RSAKey.generate_key(2048, parameters={"kid": "test"})
    provider = IdentityProvider("")

    async def discovery(request: Request) -> Response:
        return JSONResponse(
            {
                "issuer": provider.url,
                "authorization_endpoint": f"{provider.url}/authorize",
                "token_endpoint": f"{provider.url}/token",
                "jwks_uri": f"{provider.url}/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
            }
        )

    async def authorize(request: Request) -> Response:
        query = request.query_params
        back = query["redirect_uri"]
        if provider.cancel:
            return RedirectResponse(
                f"{back}?{urlencode({'error': 'access_denied', 'state': query['state']})}"
            )
        code = secrets.token_urlsafe(16)
        provider._codes[code] = {
            "client": query["client_id"],
            "nonce": query.get("nonce"),
            "challenge": query["code_challenge"],
            "user": dict(provider.user),
        }
        return RedirectResponse(f"{back}?{urlencode({'code': code, 'state': query['state']})}")

    async def granted(request: Request) -> dict[str, Any] | None:
        form = await request.form()
        grant = provider._codes.pop(str(form["code"]))
        verifier = str(form["code_verifier"]).encode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=")
        return grant if challenge.decode() == grant["challenge"] else None

    async def token(request: Request) -> Response:
        grant = await granted(request)
        if grant is None:
            return JSONResponse({"error": "invalid_grant"}, 400)
        now = int(time.time())
        claims = {"iss": provider.url, "aud": grant["client"], "iat": now, "exp": now + 300}
        claims |= {"nonce": grant["nonce"], **grant["user"]}
        signer = RSAKey.generate_key(2048) if provider.forge else key
        id_token = jwt.encode({"alg": "RS256", "kid": "test"}, claims, signer)
        return JSONResponse(
            {"access_token": "invented", "token_type": "Bearer", "id_token": id_token}
        )

    async def jwks(request: Request) -> Response:
        return JSONResponse({"keys": [key.as_dict(private=False)]})

    async def oauth_token(request: Request) -> Response:
        grant = await granted(request)
        if grant is None:
            return JSONResponse({"error": "invalid_grant"}, 400)
        access = secrets.token_urlsafe(16)
        provider._tokens[access] = {"user": grant["user"], "emails": list(provider.emails)}
        return JSONResponse({"access_token": access, "token_type": "Bearer"})

    async def user(request: Request) -> Response:
        signed_in = provider._tokens[request.headers["authorization"].removeprefix("Bearer ")]
        return JSONResponse(signed_in["user"])

    async def emails(request: Request) -> Response:
        signed_in = provider._tokens[request.headers["authorization"].removeprefix("Bearer ")]
        return JSONResponse(signed_in["emails"])

    app = Starlette(
        routes=[
            Route("/.well-known/openid-configuration", discovery),
            Route("/authorize", authorize),
            Route("/token", token, methods=["POST"]),
            Route("/jwks", jwks),
            Route("/oauth/authorize", authorize),
            Route("/oauth/token", oauth_token, methods=["POST"]),
            Route("/api/user", user),
            Route("/api/emails", emails),
        ]
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    provider.url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    yield provider
    server.should_exit = True
    await task


def form_token(page: str) -> str:
    """Return the token that an admin page's forms send back."""
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    assert found, "the page has no form"
    return found.group(1)


async def sign_in(client: httpx2.AsyncClient, base: str, next: str = "") -> httpx2.Response:
    """Sign in through the provider as its next account, and return the page that follows."""
    page = await client.get(f"{base}/admin/sign-in")
    started = await client.post(
        f"{base}/admin/sign-in", data={"csrf": form_token(page.text), "next": next}
    )
    assert started.status_code == 302, started.text
    back = await client.get(started.headers["location"])
    done = await client.get(back.headers["location"])
    assert done.status_code == 303, done.text
    return await client.get(_absolute(base, done.headers["location"]))


def _absolute(base: str, location: str) -> str:
    return location if location.startswith("http") else f"{base}{location}"
