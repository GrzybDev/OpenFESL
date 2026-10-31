from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.gift_codes import CodeChange, CodeState, GiftCode, GiftCodeStore
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.code_editor import CodeEditor, drafted, editor_of, written
from openfesl.web.sessions import check_form, toast
from openfesl.web.store_texts import StoreTexts

SECTION = "keys"
CODES = "/admin/keys/codes"
# The tabs of a game's codes besides all, each with the states it lists.
TABS = {"enabled": (CodeState.ENABLED, CodeState.USED_UP), "expired": (CodeState.EXPIRED,)}
# The buttons that answer the editor's form without saving it.
ANSWERS = ("find", "add", "drop", "again")


@dataclass(frozen=True, slots=True)
class CodeRow:
    """A code as the panel shows it.

    Attributes:
        entry: The code.
        contents: What it gives, in words.
        state: Whether it gives its gift now.
    """

    entry: GiftCode
    contents: str
    state: CodeState


class GiftCodeViews:
    """Puts together the pages of the codes that give gifts on the games' sites."""

    def __init__(
        self, sessions: AdminSessions, games: Games, store: GiftCodeStore, catalogues: Catalogues
    ) -> None:
        """Read and change the codes, which give what the games' catalogues hold.

        Args:
            sessions: Signs admins in and renders the panel's pages.
            games: The games, of which those with codes have them.
            store: Where the codes are kept.
            catalogues: The games' imported catalogues, which name the items.
        """
        self.sessions = sessions
        self.store = store
        self._games = games
        self._catalogues = catalogues

    async def list_page(self, request: Request, admin: Admin, game: Game, tab: str) -> Response:
        """Return the page of a game's codes in a tab.

        Args:
            request: The page's request.
            admin: The admin who reads it.
            game: The game, which has codes.
            tab: all, enabled, disabled or expired.
        """
        rows = await self.rows(game, request.state.language)
        counts = {name: sum(row.state in states for row in rows) for name, states in TABS.items()}
        return await self.sessions.render(
            request,
            "admin.GiftCodes",
            admin=admin,
            of=game,
            games=list(self._games),
            tab=tab,
            rows=[row for row in rows if tab not in TABS or row.state in TABS[tab]],
            counts={"all": len(rows), **counts},
            manages=admin.role is Role.ADMIN,
        )

    async def rows(self, game: Game, language: str) -> list[CodeRow]:
        """Return a game's codes, the newest first.

        Args:
            game: The game, which has codes.
            language: The language of their contents.
        """
        texts, catalogue = await self.words(game, language)
        now = datetime.now(UTC)
        return [
            CodeRow(entry, " · ".join(texts.gift_words(catalogue, entry.gift)), entry.state(now))
            for entry in await self.store.gift_codes(game.id)
        ]

    async def found(self, code: str) -> tuple[Game, GiftCode]:
        """Return a code with its game.

        Args:
            code: The code, in any case.

        Raises:
            HTTPException: 404 for no code of a game that has codes.
        """
        entry = await self.store.gift_code(code)
        game = self._games.by_id(entry.game) if entry else None
        if entry is None or game is None or game.codes is None:
            raise HTTPException(404)
        return game, entry

    def game(self, game_id: str) -> Game:
        """Return a game that has codes.

        Args:
            game_id: The game's id.

        Raises:
            HTTPException: 404 for a game without codes.
        """
        game = self._games.by_id(game_id)
        if game is None or game.codes is None:
            raise HTTPException(404)
        return game

    async def editor(
        self,
        game: Game,
        language: str,
        fields: ImmutableMultiDict[str, Any] | None,
        saving: bool = False,
    ) -> CodeEditor:
        """Return a new code's editor, with its fields as written where a form sent them.

        The code is drawn at first and again when the form asks for another.

        Args:
            game: The game, which has codes.
            language: The page's language.
            fields: The form's fields; none for a new editor.
            saving: Whether the form is saved, which checks every field.
        """
        rules = game.codes
        assert rules is not None
        texts, catalogue = await self.words(game, language)
        if fields is None:
            draft = drafted(rules.generate())
        else:
            draft = written(fields)
            if "again" in fields or rules.read(draft.code) != draft.code:
                draft = replace(draft, code=rules.generate())
        adding = fields is not None and "add" in fields
        return editor_of(texts, catalogue, draft, adding, saving)

    async def words(self, game: Game, language: str) -> tuple[StoreTexts, Catalogue]:
        """Return what writes a game's codes in a language, and its catalogue in that language.

        Args:
            game: The game.
            language: The language.
        """
        texts = StoreTexts(game, language, datetime.now(UTC))
        return texts, await self._catalogues.of(game, language)


def code_url(request: Request, code: str) -> str:
    """Return the address of a code's page in the request's language.

    Args:
        request: The request.
        code: The code.
    """
    return localize(f"{CODES}/{quote(code)}", request.state.language)


def gift_code_routes(views: GiftCodeViews) -> APIRouter:
    """Return the pages that make and show the codes that give gifts.

    Every role reads the codes in full; making them needs the admin role and a note, which the
    audit log keeps. The item search, adding and dropping
    items and drawing another code answer the editor's form without saving it.

    Args:
        views: Put together what the pages show and keep the codes.
    """
    router = APIRouter()
    sessions = views.sessions

    async def editor_page(
        request: Request, admin: Admin, game: Game, status: int = 200
    ) -> HTMLResponse:
        # Live answers to the fields as they are typed leave out the buttons that change items.
        query = request.query_params.multi_items()
        typed = [(name, value) for name, value in query if name not in ("add", "drop", "again")]
        fields: ImmutableMultiDict[str, Any] | None = (
            await request.form() if request.method == "POST" else ImmutableMultiDict(typed)
        )
        if request.method != "POST" and not [name for name, _ in typed if name != "game"]:
            fields = None
        found = await views.editor(game, request.state.language, fields, status == 400)
        return await sessions.render(
            request, "admin.GiftCodeForm", status, admin=admin, of=game, found=found
        )

    @router.get(CODES + "/new", response_class=HTMLResponse)
    async def new(request: Request, game: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        return await editor_page(request, admin, views.game(game))

    @router.post(CODES + "/new")
    async def make(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await request.form()
        check_form(request, form)
        game = views.game(str(form.get("game", "")))
        if any(name in form for name in ANSWERS):
            return await editor_page(request, admin, game)
        found = await views.editor(game, request.state.language, form, saving=True)
        terms = found.terms
        if terms is None:
            return await editor_page(request, admin, game, 400)
        code = found.draft.code
        if not await views.store.make(game.id, code, terms, CodeChange(admin, found.draft.note)):
            toast(request, "web.admin.toast.code.taken", error=True)
            return await editor_page(request, admin, game)
        toast(request, "web.admin.toast.code.made", code=code)
        return RedirectResponse(code_url(request, code), 303)

    @router.get(CODES + "/{code}", response_class=HTMLResponse)
    async def code_page(request: Request, code: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game, entry = await views.found(code)
        texts, catalogue = await views.words(game, request.state.language)
        return await sessions.render(
            request,
            "admin.GiftCode",
            admin=admin,
            of=game,
            entry=entry,
            state=entry.state(datetime.now(UTC)),
            contents=texts.gift_words(catalogue, entry.gift),
            made=await views.store.made(entry.code),
            manages=admin.role is Role.ADMIN,
        )

    return router
