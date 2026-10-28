from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from openfesl.core.admin import ActionHistory, AdminAction, AuditEntry
from openfesl.core.admins import Admin, Role
from openfesl.core.catalogue import FORMAT, CatalogueProblem, CatalogueRefused
from openfesl.core.catalogues import Catalogues, Contents
from openfesl.core.game_art import ArtViewers, GameArt
from openfesl.core.games import Game, Games
from openfesl.core.mugshots import Mugshots
from openfesl.core.texts import localize
from openfesl.formats.catalogue_archive import MAX_SIZE
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.pages import Toast, language_name
from openfesl.web.sessions import check_form, toast

SECTION = "catalogues"
# What the last import of the page brought, until the page shows it.
RESULT = "catalogue_result"
# The form's other fields and boundaries, which a body holds beside the file.
FORM_SLACK = 64 * 1024


@dataclass(frozen=True, slots=True)
class ArtView:
    """Who sees a game's art, as its block on the catalogues page shows it.

    Attributes:
        viewers: Who sees it.
        chosen: The latest choice as the admin's log records it; none while the default holds.
        portraits: Whether the choice covers the portraits alone, as no icons or hero models are
            kept.
        models: Whether hero models are kept, which the creator's stage shows.
    """

    viewers: ArtViewers
    chosen: AuditEntry | None
    portraits: bool
    models: bool


@dataclass(frozen=True, slots=True)
class CatalogueView:
    """A game's catalogue as its block on the catalogues page shows it.

    Attributes:
        game: The game.
        contents: What its imported catalogue holds; nothing before the first import.
        kinds: How many items of each kind it holds, by kind, most first.
        languages: The names of the languages it has texts in, in the page's language and order.
        imported: Its import as the admin's log records it, if it does.
        art: Who sees the game's art.
    """

    game: Game
    contents: Contents
    kinds: list[tuple[str, int]]
    languages: list[str]
    imported: AuditEntry | None
    art: ArtView


def catalogue_routes(
    sessions: AdminSessions,
    games: Games,
    catalogues: Catalogues,
    log: ActionHistory,
    keeps_files: bool,
    apply_actions: Callable[[], Awaitable[None]],
    art: GameArt,
    mugshots: Mugshots,
) -> APIRouter:
    """Return the catalogues page: each game's imported catalogue, who sees its art, and the import.

    The file names its game, so the page has one import for all of them. Moderators see the
    catalogues; importing and choosing who sees the art need the admin role. An import goes on
    once its file is sent, also when the browser leaves the page; a choice of who sees the art
    takes effect at once.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, of which those with catalogue rules take a catalogue.
        catalogues: The games' imported catalogues, which an import replaces.
        log: Says who imported each catalogue and when.
        keeps_files: Whether the deployment keeps the icons and hero models of an import.
        apply_actions: Hands the admin's recorded actions to the running server, such as an
            import to the store.
        art: Says to whom each game's art is shown, which the page changes.
        mugshots: Counts the portraits that each game rendered, which the art covers too.
    """
    router = APIRouter()
    taking = [game for game in games if game.catalogue is not None]

    async def view(game: Game, language: str) -> CatalogueView:
        contents = catalogues.contents(game)
        order = {kind: index for index, kind in enumerate(_kinds(game))}
        counted = Counter(item.kind for item in contents.items.values())
        kinds = sorted(counted.items(), key=lambda kind: (-kind[1], order.get(kind[0], 0)))
        names = sorted(language_name(code, language) for code in contents.languages)
        imported = await log.latest(AdminAction.CATALOGUE_IMPORT, game.id)
        shown = ArtView(
            art.viewers(game),
            await log.latest(AdminAction.CATALOGUE_ART, game.id),
            not (contents.icons or contents.models),
            contents.models > 0,
        )
        return CatalogueView(
            game, contents, kinds, names, imported if contents.revision else None, shown
        )

    @router.get("/admin/catalogues", response_class=HTMLResponse)
    async def page(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        result: dict[str, Any] | None = request.session.pop(RESULT, None)
        views = [await view(game, request.state.language) for game in taking]
        return await sessions.render(
            request,
            "admin.Catalogues",
            admin=admin,
            views=views,
            others=[game for game in games if game.catalogue is None],
            manages=admin.role is Role.ADMIN,
            keeps_files=keeps_files,
            result=result,
            most=MAX_SIZE,
            failed=Toast("web.toast.failed", {}, error=True),
        )

    @router.post("/admin/catalogues")
    async def import_catalogue(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        back = localize("/admin/catalogues", request.state.language) + "#import"
        # Browsers send the length of a form, so a body too long for a catalogue goes unparsed.
        length = int(request.headers.get("content-length") or MAX_SIZE + FORM_SLACK + 1)
        if length > MAX_SIZE + FORM_SLACK:
            async for _ in request.stream():
                pass
            request.session[RESULT] = {"kind": "large", "size": length}
            return RedirectResponse(back, 303)
        try:
            form = await request.form(max_files=1, max_fields=3)
        except ClientDisconnect:
            # The browser stopped sending the file, so nothing is imported or shown.
            return Response(status_code=400)
        try:
            check_form(request, form)
            upload = form.get("file")
            if not isinstance(upload, UploadFile) or not upload.filename:
                request.session[RESULT] = {"kind": "needed"}
                return RedirectResponse(back, 303)
            name, data = upload.filename, await upload.read()
        finally:
            await form.close()
        request.session[RESULT] = await _import(catalogues, games, data, name, admin)
        await apply_actions()
        return RedirectResponse(back, 303)

    @router.post("/admin/catalogues/{game_id}/art")
    async def choose_art(request: Request, game_id: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = games.by_id(game_id)
        if game is None or game.catalogue is None:
            raise HTTPException(404)
        try:
            viewers = ArtViewers(str(form.get("shown", "")))
        except ValueError:
            raise HTTPException(400) from None
        if viewers is not art.viewers(game):
            contents = catalogues.contents(game)
            covers = {
                "icons": contents.icons,
                "models": contents.models,
                "textures": contents.textures,
                "portraits": await mugshots.count(game),
            }
            await art.choose(game, viewers, covers, admin)
        toast(request, f"web.admin.catalogue.art.saved.{viewers.value}", game=game.name)
        back = localize("/admin/catalogues", request.state.language)
        return RedirectResponse(f"{back}#art-{game.id}", 303)

    return router


async def _import(
    catalogues: Catalogues, games: Games, data: bytes, name: str, admin: Admin
) -> dict[str, Any]:
    # What the page says of the import: the numbers it changed, or why the file was refused.
    before = {game.id: _numbers(catalogues.contents(game)) for game in games}
    try:
        imported = await catalogues.import_file(data, admin)
    except CatalogueRefused as refused:
        named = games.by_id(refused.detail) if refused.problem is CatalogueProblem.GAME else None
        return {
            "kind": "refused",
            "problem": refused.problem.value,
            "detail": named.name if named else refused.detail,
            "read": FORMAT,
            "file": name,
        }
    game = imported.game
    earlier = before.get(game.id)
    return {
        "kind": "imported",
        "game": game.id,
        "before": earlier if earlier and earlier["revision"] else None,
        "after": _numbers(catalogues.contents(game)),
    }


def _numbers(contents: Contents) -> dict[str, Any]:
    # The numbers of a catalogue that the result of an import compares.
    return {
        "revision": contents.revision,
        "items": len(contents.items),
        "languages": len(contents.languages),
        "icons": contents.icons,
        "models": contents.models,
        "textures": contents.textures,
    }


def _kinds(game: Game) -> list[str]:
    # The kinds of a game's items in the order its rules name them.
    rules = game.catalogue
    return list(dict.fromkeys(item.kind for item in rules.items)) if rules else []
