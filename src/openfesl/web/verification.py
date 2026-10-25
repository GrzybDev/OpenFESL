from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.verification import PATH, Confirmation
from openfesl.web.pages import Pages

_STATUS = {Confirmation.CONFIRMED: 200, Confirmation.EXPIRED: 410, Confirmation.UNKNOWN: 404}


def verification_routes(
    confirm: Callable[[str], Awaitable[Confirmation]], pages: Pages
) -> APIRouter:
    """Return the page that the links confirming new accounts' e-mail addresses lead to.

    Args:
        confirm: Confirms the address of the account that a link's token was sent to.
        pages: Renders the page.
    """
    router = APIRouter()

    @router.get(PATH, response_class=HTMLResponse)
    async def verify(request: Request, token: str) -> HTMLResponse:
        confirmation = await confirm(token)
        # The token is a secret, so neither caches nor other sites may keep the page's address.
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        return pages.render(
            request,
            "Verification",
            _STATUS[confirmation],
            # Opening the link again in another language would find it used.
            switch=False,
            headers=headers,
            state=confirmation.name.lower(),
        )

    return router
