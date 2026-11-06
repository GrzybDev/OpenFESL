import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.datastructures import ImmutableMultiDict
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.catalogues import Catalogue, Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.gift_codes import (
    MOST_BATCH,
    CodeChange,
    CodeState,
    CodeTerms,
    GiftCode,
    GiftCodeStore,
    Redemption,
)
from openfesl.core.reward_rules import Gift
from openfesl.core.texts import localize
from openfesl.web.admin.auth import AdminSessions, game_among
from openfesl.web.admin.code_editor import CodeEditor, drafted, editor_of, written
from openfesl.web.pages import whole_number
from openfesl.web.sessions import checked_form, toast
from openfesl.web.store_texts import StoreTexts

SECTION = "keys"
CODES = "/admin/keys/codes"
# The tabs of a game's codes besides all, each with the states it lists.
TABS = {
    "enabled": (CodeState.ENABLED, CodeState.USED_UP),
    "disabled": (CodeState.DISABLED,),
    "expired": (CodeState.EXPIRED,),
}
# The buttons that answer the editor's form without saving it.
ANSWERS = ("find", "add", "drop", "again")
# How many of a batch's codes its page shows; the download holds them all.
SHOWN = 5


@dataclass(frozen=True, slots=True)
class CodeRow:
    """A code or batch as the panel shows it.

    Attributes:
        entry: The code or batch.
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
        """Return the page of a game's codes in a tab, a batch as one row.

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
        )

    async def rows(self, game: Game, language: str) -> list[CodeRow]:
        """Return a game's codes, a batch as one, the newest first.

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
        """Return a code or the batch it belongs to, with its game.

        Args:
            code: The code, in any case.

        Raises:
            HTTPException: 404 for no code of a game that has codes.
        """
        entry = await self.store.gift_code(code)
        if entry is None:
            raise HTTPException(404)
        return self.game(entry.game), entry

    def game(self, game_id: str) -> Game:
        """Return a game that has codes.

        Args:
            game_id: The game's id.

        Raises:
            HTTPException: 404 for a game without codes.
        """
        return game_among((game for game in self._games if game.codes), game_id)

    async def editor(
        self,
        game: Game,
        entry: GiftCode | None,
        language: str,
        fields: ImmutableMultiDict[str, Any] | None,
        saving: bool = False,
    ) -> CodeEditor:
        """Return a code's editor, with its fields as written where a form sent them.

        A new single code is drawn at first and again when the form asks for another.

        Args:
            game: The game, which has codes.
            entry: The code or batch being changed; none for a new one.
            language: The page's language.
            fields: The form's fields; none for the code as it is saved, or a new one.
            saving: Whether the form is saved, which checks every field.
        """
        rules = game.codes
        assert rules is not None
        texts, catalogue = await self.words(game, language)
        if fields is None:
            draft = drafted(entry, rules.generate())
        else:
            draft = written(fields)
            if entry is None and ("again" in fields or rules.read(draft.code) != draft.code):
                draft = replace(draft, code=rules.generate())
        adding = fields is not None and "add" in fields
        return editor_of(texts, catalogue, entry, draft, adding, saving)

    async def words(self, game: Game, language: str) -> tuple[StoreTexts, Catalogue]:
        """Return what writes a game's codes in a language, and its catalogue in that language.

        Args:
            game: The game.
            language: The language.
        """
        texts = StoreTexts(game, language)
        return texts, await self._catalogues.of(game, language)

    async def redeemed(self, account_id: int, language: str) -> list[tuple[Redemption, str]] | None:
        """Return the codes an account redeemed, the newest first, with what each gave.

        Args:
            account_id: The account.
            language: The language of the words.

        Returns:
            The redemptions, or None where no game's site redeems codes.
        """
        games = [game for game in self._games if game.codes]
        if not games:
            return None
        found = []
        for game in games:
            texts, catalogue = await self.words(game, language)
            found += [
                (done, given(texts, catalogue, done))
                for done in await self.store.redeemed_by(game.id, account_id)
            ]
        return sorted(found, key=lambda pair: pair[0].at, reverse=True)


def given(texts: StoreTexts, catalogue: Catalogue, done: Redemption) -> str:
    """Return what a redemption gave in words: items with what the hero held after, Funds, keys.

    Args:
        texts: Writes the page's language for the code's game.
        catalogue: The game's imported catalogue, which names the items.
        done: The redemption.
    """
    words = []
    for item in done.items:
        name = catalogue.name(item.item_id) or texts.text(
            "web.admin.items.unnamed", id=item.item_id
        )
        what = texts.text(
            "web.deals.card.item", item=name, ownership=texts.ownership(item.ownership, inline=True)
        )
        if item.fallback:
            what = texts.text("web.admin.keys.given.fallback", item=what)
        elif left := texts.left(item.held):
            what = texts.text("web.admin.keys.given.left", item=what, left=left)
        words.append(what)
    gift = Gift(funds=done.funds[0] if done.funds else 0, keys=done.keys[0] if done.keys else 0)
    return " · ".join(words + texts.gift_words(catalogue, gift))


def code_url(request: Request, code: str) -> str:
    """Return the address of a code's page in the request's language.

    Args:
        request: The request.
        code: The code, or a batch's first code.
    """
    return localize(f"{CODES}/{quote(code)}", request.state.language)


def gift_code_routes(views: GiftCodeViews) -> APIRouter:
    """Return the pages that make, show, change, enable and disable the codes that give gifts.

    Every role reads the codes in full and downloads a batch's; making and changing them needs
    the admin role and a note, which the audit log keeps. The item search, adding and dropping
    items and drawing another code answer the editor's form without saving it.

    Args:
        views: Put together what the pages show and keep the codes.
    """
    router = APIRouter()
    sessions = views.sessions

    async def editor_page(
        request: Request, admin: Admin, game: Game, entry: GiftCode | None, status: int = 200
    ) -> HTMLResponse:
        # Live answers to the fields as they are typed leave out the buttons that change items.
        query = request.query_params.multi_items()
        typed = [(name, value) for name, value in query if name not in ("add", "drop", "again")]
        fields: ImmutableMultiDict[str, Any] | None = (
            await request.form() if request.method == "POST" else ImmutableMultiDict(typed)
        )
        if request.method != "POST" and not [name for name, _ in typed if name != "game"]:
            fields = None
        found = await views.editor(game, entry, request.state.language, fields, status == 400)
        return await sessions.render(
            request, "admin.GiftCodeForm", status, admin=admin, of=game, found=found
        )

    @router.get(CODES + "/new", response_class=HTMLResponse)
    async def new(request: Request, game: str = "") -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        return await editor_page(request, admin, views.game(game), None)

    @router.post(CODES + "/new")
    async def make(request: Request) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = views.game(str(form.get("game", "")))
        if any(name in form for name in ANSWERS):
            return await editor_page(request, admin, game, None)
        found = await views.editor(game, None, request.state.language, form, saving=True)
        terms = found.terms
        if terms is None:
            return await editor_page(request, admin, game, None, 400)
        assert game.codes is not None
        count = whole_number(found.draft.count, MOST_BATCH) if found.batch else 1
        assert count is not None
        codes = [game.codes.generate() for _ in range(count)] if found.batch else [found.draft.code]
        if not await views.store.make(game.id, codes, terms, CodeChange(admin, found.draft.note)):
            toast(request, "web.admin.toast.code.taken", error=True)
            return await editor_page(request, admin, game, None)
        if found.batch:
            toast(request, "web.admin.toast.code.batch", count=str(count))
        else:
            toast(request, "web.admin.toast.code.made", code=codes[0])
        return RedirectResponse(code_url(request, codes[0]), 303)

    @router.get(CODES + "/{code}", response_class=HTMLResponse)
    async def code_page(request: Request, code: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game, entry = await views.found(code)
        texts, catalogue = await views.words(game, request.state.language)
        redemptions = await views.store.redemptions(entry)
        assert game.site is not None
        return await sessions.render(
            request,
            "admin.GiftCode",
            admin=admin,
            of=game,
            entry=entry,
            state=entry.state(datetime.now(UTC)),
            contents=texts.gift_words(catalogue, entry.gift),
            made=await views.store.made(entry.code),
            codes=entry.codes[:SHOWN] if entry.batch else (),
            more=max(entry.count - SHOWN, 0) if entry.batch else 0,
            redemptions=[(done, given(texts, catalogue, done)) for done in redemptions],
            link=f"/{game.id}{game.site.path('redeem')}?{urlencode({'key': entry.code})}",
        )

    @router.get(CODES + "/{code}/codes.txt")
    async def download(request: Request, code: str) -> Response:
        sessions.admin(request)
        _, entry = await views.found(code)
        if not entry.batch:
            raise HTTPException(404)
        name = re.sub(r"[^a-z0-9]+", "-", entry.label("en").lower()).strip("-") or "codes"
        return PlainTextResponse(
            "".join(f"{found}\n" for found in entry.codes),
            headers={"Content-Disposition": f'attachment; filename="{name}.txt"'},
        )

    @router.get(CODES + "/{code}/edit", response_class=HTMLResponse)
    async def edit(request: Request, code: str) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game, entry = await views.found(code)
        return await editor_page(request, admin, game, entry)

    @router.post(CODES + "/{code}/edit")
    async def save(request: Request, code: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game, entry = await views.found(code)
        if any(name in form for name in ANSWERS):
            return await editor_page(request, admin, game, entry)
        found = await views.editor(game, entry, request.state.language, form, saving=True)
        if found.terms is None:
            return await editor_page(request, admin, game, entry, 400)
        change = CodeChange(admin, found.draft.note, {"changed": changes(entry, found.terms)})
        await views.store.change(entry, found.terms, change)
        toast(request, "web.admin.toast.code.saved", code=entry.code)
        return RedirectResponse(code_url(request, entry.code), 303)

    @router.get(CODES + "/{code}/state", response_class=HTMLResponse)
    async def ask_state(request: Request, code: str) -> HTMLResponse:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game, entry = await views.found(code)
        return await sessions.render(
            request, "admin.GiftCodeState", admin=admin, of=game, entry=entry, note="", error=""
        )

    @router.post(CODES + "/{code}/state")
    async def switch(request: Request, code: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game, entry = await views.found(code)
        note = str(form.get("note", "")).strip()
        if not note:
            return await sessions.render(
                request,
                "admin.GiftCodeState",
                400,
                admin=admin,
                of=game,
                entry=entry,
                note=note,
                error="note",
            )
        # The state the page asked for, so that a page left open changes nothing twice.
        enabled = form.get("enabled") == "1"
        if await views.store.switch(entry, enabled, CodeChange(admin, note)):
            done = "enabled" if enabled else "disabled"
            toast(request, f"web.admin.toast.code.{done}", code=entry.code)
        else:
            toast(request, "web.admin.toast.key_unchanged", error=True, code=entry.code)
        return RedirectResponse(code_url(request, entry.code), 303)

    return router


def changes(entry: GiftCode, terms: CodeTerms) -> dict[str, list[Any]]:
    """Return what an edit changes of a code, each as its value before and after.

    Args:
        entry: The code or batch as it is.
        terms: What it gives and on what terms after the edit.
    """
    expires = [entry.expires, terms.expires]
    found = {
        "gift": [entry.gift.written, terms.gift.written],
        "max_uses": [entry.max_uses, terms.max_uses] if not entry.batch else [None, None],
        "expires": [moment.isoformat() if moment else None for moment in expires],
        "labels": [dict(entry.labels), dict(terms.labels)],
    }
    return {name: values for name, values in found.items() if values[0] != values[1]}
