from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from openfesl.core.registration import Terms
from openfesl.core.texts import translate
from openfesl.web.pages import KEEP, STATIC, Pages


def shell_routes(pages: Pages, terms: Terms) -> APIRouter:
    """Return the pages that belong to no game, the games' themes and OpenFESL's icon.

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

    @router.get("/themes.css")
    async def themes(request: Request) -> Response:
        headers = {"Cache-Control": KEEP} if "v" in request.query_params else None
        return Response(pages.themes, media_type="text/css", headers=headers)

    # Asked for by browsers that skip the pages' icon links, and for addresses that are no page.
    @router.get("/favicon.ico")
    async def favicon() -> FileResponse:
        return FileResponse(STATIC / "favicon-32.png", media_type="image/png")

    return router
