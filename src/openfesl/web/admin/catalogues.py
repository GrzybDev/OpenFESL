from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.admin import AdminAction, AuditEntry
from openfesl.core.catalogues import Catalogues, Contents
from openfesl.core.games import Game, Games
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.pages import language_name


class ImportLog(Protocol):
    """Where the page reads who imported each game's catalogue and when."""

    async def latest(self, action: AdminAction, target: str) -> AuditEntry | None:
        """Return the latest action of a kind done to a target, if any was.

        Args:
            action: The kind.
            target: What it was done to.
        """


@dataclass(frozen=True, slots=True)
class CatalogueView:
    """A game's catalogue as its block on the catalogues page shows it.

    Attributes:
        game: The game.
        contents: What its imported catalogue holds; nothing before the first import.
        kinds: How many items of each kind it holds, by kind, most first.
        languages: The names of the languages it has texts in, in the page's language and order.
        imported: Its import as the admin's log records it, if it does.
    """

    game: Game
    contents: Contents
    kinds: list[tuple[str, int]]
    languages: list[str]
    imported: AuditEntry | None


def catalogue_routes(
    sessions: AdminSessions,
    games: Games,
    catalogues: Catalogues,
    log: ImportLog,
    keeps_files: bool,
) -> APIRouter:
    """Return the catalogues page: what each game's imported catalogue holds.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, of which those with catalogue rules take a catalogue.
        catalogues: The games' imported catalogues.
        log: Says who imported each catalogue and when.
        keeps_files: Whether the deployment keeps the icons and hero models of an import.
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
        return CatalogueView(game, contents, kinds, names, imported if contents.revision else None)

    @router.get("/admin/catalogues", response_class=HTMLResponse)
    async def page(request: Request) -> HTMLResponse:
        admin = sessions.admin(request)
        views = [await view(game, request.state.language) for game in taking]
        return await sessions.render(
            request,
            "admin.Catalogues",
            admin=admin,
            views=views,
            others=[game for game in games if game.catalogue is None],
            keeps_files=keeps_files,
        )

    return router


def _kinds(game: Game) -> list[str]:
    # The kinds of a game's items in the order its rules name them.
    rules = game.catalogue
    return list(dict.fromkeys(item.kind for item in rules.items)) if rules else []
