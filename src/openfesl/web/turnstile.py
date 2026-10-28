import logging
from collections.abc import Callable, Mapping
from typing import Any

import httpx2
from fastapi import Request
from fastapi.responses import Response

from openfesl.core.connection import Address
from openfesl.web.security import allow

logger = logging.getLogger(__name__)

ORIGIN = "https://challenges.cloudflare.com"
SITEVERIFY = f"{ORIGIN}/turnstile/v0/siteverify"
# The form field in which Cloudflare's widget puts the token of a passed check.
FIELD = "cf-turnstile-response"
TIMEOUT = 10.0


class Turnstile:
    """Cloudflare Turnstile's check of the forms that take an e-mail address or send mail."""

    def __init__(
        self,
        site_key: str,
        secret_key: str,
        client_address: Callable[[Request], Address | None],
    ) -> None:
        """Check forms with the deployment's Turnstile keys.

        Args:
            site_key: The key that the pages' widget shows the check with.
            secret_key: The key that the server checks the widget's tokens with.
            client_address: Returns the address of the client behind a request, which
                Cloudflare compares with the one that passed the check.
        """
        self.site_key = site_key
        self._secret_key = secret_key
        self._client_address = client_address

    def allow(self, response: Response) -> None:
        """Let a page that shows the check load Cloudflare's script and frame.

        Args:
            response: The page.
        """
        allow(response, "script-src", ORIGIN)
        allow(response, "frame-src", ORIGIN)

    async def passed(self, request: Request, form: Mapping[str, Any]) -> bool:
        """Return whether a form passed the check, asking Cloudflare about its token.

        A token that Cloudflare does not confirm, or no answer from Cloudflare, fails the form,
        which then comes back with a new check.

        Args:
            request: The form's request.
            form: The form's fields.
        """
        token = form.get(FIELD)
        if not isinstance(token, str) or not token:
            return False
        sent = {"secret": self._secret_key, "response": token}
        if address := self._client_address(request):
            sent["remoteip"] = str(address)
        try:
            async with httpx2.AsyncClient(timeout=TIMEOUT) as client:
                answer = await client.post(SITEVERIFY, data=sent)
                answer.raise_for_status()
                return answer.json().get("success") is True
        except (httpx2.HTTPError, ValueError) as error:
            logger.warning("Cloudflare Turnstile could not check a form: %r", error)
            return False
