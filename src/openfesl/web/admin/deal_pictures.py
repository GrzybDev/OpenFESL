from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from openfesl.core.admins import Role
from openfesl.core.games import Game
from openfesl.formats.deal_art import fits
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.deals import DealAdminViews, HomeDeal, deal_page
from openfesl.web.admin.store import SECTION, store_url
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

# The largest picture an upload takes; the game's own are a few hundred kilobytes at most.
MOST_PICTURE = 1024 * 1024


def deal_picture_routes(sessions: AdminSessions, views: DealAdminViews) -> APIRouter:
    """Return the pictures of the hot deals that the games' home screens show, and their uploads.

    Every role sees the pictures the game downloads; uploading and resetting them needs the admin
    role. An uploaded picture goes with the deal's offer and takes the drawn one's place.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and keep the uploads.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/deals/art/{number}"

    async def listed(game: Game, number: int, language: str) -> HomeDeal:
        shown = await views.home(game, language)
        found = next((deal for deal in shown if deal.deal.listed.id == number), None)
        if found is None:
            raise HTTPException(404)
        return found

    @router.get(path + "_{size}.png")
    async def picture(request: Request, game_id: str, number: int, size: str) -> Response:
        sessions.admin(request)
        game = views.stores.game(game_id)
        if size not in ("big", "small"):
            raise HTTPException(404)
        drawn = await views.art.picture(game, number, size == "big", shown=True)
        if drawn is None:
            raise HTTPException(404)
        return Response(drawn, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.post(path)
    async def upload(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.stores.game(game_id)
        form = await request.form(max_files=2, max_fields=2)
        try:
            check_form(request, form)
            files = {
                big: (sent.filename or "", await sent.read(MOST_PICTURE + 1))
                for big, sent in ((True, form.get("big")), (False, form.get("small")))
                if isinstance(sent, UploadFile) and sent.filename
            }
        finally:
            await form.close()
        shown = await listed(game, number, request.state.language)
        texts = StoreTexts(game, request.state.language, datetime.now(UTC))
        for big, (name, data) in files.items():
            if len(data) > MOST_PICTURE or not fits(data, big):
                key = f"web.admin.deals.art.refused.{'big' if big else 'small'}"
                why = (number, texts.text(key, file=name))
                return await deal_page(sessions, views, request, admin, game, "", {}, "", why)
        for big, (_, data) in files.items():
            await views.uploads.put(game.id, shown.offer, big, data, admin)
        if files:
            toast(request, "web.admin.toast.deals.art", deal=shown.deal.name)
        return RedirectResponse(store_url(request, game, "/deals#home"), 303)

    @router.post(path + "/reset")
    async def reset(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.stores.game(game_id)
        check_form(request, await request.form())
        shown = await listed(game, number, request.state.language)
        if await views.uploads.remove(game.id, shown.offer, admin):
            toast(request, "web.admin.toast.deals.art.reset", deal=shown.deal.name)
        return RedirectResponse(store_url(request, game, "/deals#home"), 303)

    return router
