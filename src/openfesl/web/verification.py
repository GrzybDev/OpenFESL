from collections.abc import Awaitable, Callable

from babel import negotiate_locale
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from openfesl.core.texts import BASE_LANGUAGE, languages, translate
from openfesl.core.verification import PATH, Confirmation

_STATUS = {Confirmation.CONFIRMED: 200, Confirmation.EXPIRED: 410, Confirmation.UNKNOWN: 404}
_templates = Environment(loader=PackageLoader("openfesl.web"), autoescape=select_autoescape())


def verification_routes(confirm: Callable[[str], Awaitable[Confirmation]]) -> APIRouter:
    """Return the page that the links confirming new accounts' e-mail addresses lead to.

    Args:
        confirm: Confirms the address of the account that a link's token was sent to.
    """
    router = APIRouter()

    @router.get(PATH, response_class=HTMLResponse)
    async def verify(request: Request, token: str) -> HTMLResponse:
        confirmation = await confirm(token)
        language = _language(request.headers.get("Accept-Language", ""))
        state = confirmation.name.lower()
        page = _templates.get_template("verification.html").render(
            language=language,
            heading=translate(f"verification.page.{state}.heading", language),
            text=translate(f"verification.page.{state}.text", language),
        )
        # The token is a secret, so neither caches nor other sites may keep the page's address.
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        return HTMLResponse(page, _STATUS[confirmation], headers)

    return router


def _language(accepted: str) -> str:
    # Browsers list their languages by preference, so the quality values add nothing.
    preferred = [part.split(";")[0].strip() for part in accepted.split(",") if part.strip()]
    return negotiate_locale(preferred, languages(), sep="-") or BASE_LANGUAGE
