from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.registration import Terms
from openfesl.core.texts import translate
from openfesl.web.pages import Pages


def shell_routes(pages: Pages, terms: Terms) -> APIRouter:
    """Return the pages that belong to no game.

    Args:
        pages: Renders the pages.
        terms: The terms the games show on registration.
    """
    router = APIRouter()

    @router.get("/terms", response_class=HTMLResponse)
    async def show_terms(request: Request) -> HTMLResponse:
        language = terms.language(request.state.language)
        return pages.render(
            request,
            "Terms",
            version=terms.version,
            # Named in its own language, as the shell names languages.
            text_language=translate("web.language.name", language),
            paragraphs=terms.text(language).split("\n\n"),
        )

    return router
