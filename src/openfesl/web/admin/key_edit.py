from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.entitlements import KeyHistory, KeyLimits, StoredKey
from openfesl.core.games import Games, Grant
from openfesl.core.texts import languages, localize
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.keys import SECTION, KeyList
from openfesl.web.keys import KeyView, key_views

MAXIMUM_LABEL = 80


class KeyEditor(Protocol):
    """Where the admin panel reads product keys and changes what they grant."""

    async def keys(self) -> list[StoredKey]:
        """Return every product key with what it grants, in the order they were added."""

    async def key_history(self) -> dict[str, KeyHistory]:
        """Return when each product key was added and last enabled or disabled by the admin."""

    async def edit_key(
        self,
        code: str,
        grants: Sequence[Grant],
        labels: Mapping[str, str],
        limits: KeyLimits,
        admin: Admin,
    ) -> None:
        """Replace what a product key grants, its labels and its limits.

        Args:
            code: The key as stored.
            grants: What it grants from now on, each tag once.
            labels: Its public labels by language.
            limits: How often it may be redeemed from now on.
            admin: The admin who changed it.
        """


@dataclass(frozen=True, slots=True)
class GrantRow:
    """One row of the grants a key's form holds, as typed.

    Attributes:
        tag: The tag.
        group: The group, if any.
        product: The product id, if any.
        removed: Whether the row's remove box is ticked.
    """

    tag: str = ""
    group: str = ""
    product: str = ""
    removed: bool = False

    @property
    def empty(self) -> bool:
        """Whether nothing is typed in it."""
        return not (self.tag or self.group or self.product)


@dataclass(frozen=True, slots=True)
class KeyForm:
    """What a key's form holds: its grants, labels and limits, and what is wrong with them.

    Attributes:
        rows: The grants, one row per grant and a last empty one for a new grant.
        labels: The public labels by language.
        max_uses: How many accounts may enter the key, as typed.
        once: Whether an account may enter it only once.
        error: What is wrong: no-tag, repeated, uses or label; empty when nothing is.
        row: The number of the row the error is about, from 1.
    """

    rows: tuple[GrantRow, ...]
    labels: Mapping[str, str]
    max_uses: str
    once: bool
    error: str = ""
    row: int = 0

    @classmethod
    def of(cls, view: KeyView) -> KeyForm:
        """Return the form as the key is now.

        Args:
            view: The key.
        """
        key = view.key
        rows = [GrantRow(grant.tag, grant.group or "", grant.product or "") for grant in key.grants]
        labels = {language: view.label(language) or "" for language in languages()}
        most = key.limits.max_uses
        return cls(
            (*rows, GrantRow()), labels, str(most) if most else "", key.limits.once_per_account
        )

    @classmethod
    def read(cls, form: FormData) -> KeyForm:
        """Read the form as sent and check it.

        Args:
            form: The form's fields.
        """
        lists = [form.getlist(name) for name in ("tag", "group", "product")]
        removed = set(form.getlist("remove"))
        rows = tuple(
            GrantRow(
                str(tag).strip(), str(group).strip(), str(product).strip(), str(number) in removed
            )
            for number, (tag, group, product) in enumerate(zip(*lists, strict=False), 1)
        )
        labels = {
            language: str(form.get(f"label.{language}", "")).strip() for language in languages()
        }
        found = cls(rows, labels, str(form.get("max_uses", "")).strip(), form.get("once") == "1")
        seen: dict[str, int] = {}
        for number, row in enumerate(rows, 1):
            if row.removed or row.empty:
                continue
            if not row.tag:
                return found.failed("no-tag", number)
            if row.tag in seen:
                return found.failed("repeated", number)
            seen[row.tag] = number
        if found.max_uses and not (found.max_uses.isdigit() and int(found.max_uses) > 0):
            return found.failed("uses")
        if any(len(label) > MAXIMUM_LABEL for label in labels.values()):
            return found.failed("label")
        return found

    def failed(self, error: str, row: int = 0) -> KeyForm:
        """Return the form with what is wrong with it.

        Args:
            error: What is wrong.
            row: The number of the row it is about, if any.
        """
        return KeyForm(self.rows, self.labels, self.max_uses, self.once, error, row)

    def grants(self) -> list[Grant]:
        """Return the grants the form keeps, without removed and empty rows."""
        return [
            Grant(tag=row.tag, group=row.group or None, product=row.product or None)
            for row in self.rows
            if not row.removed and not row.empty
        ]


def key_edit_routes(sessions: AdminSessions, games: Games, store: KeyEditor) -> APIRouter:
    """Return the page that edits a product key's grants, public labels and use limits.

    It needs the admin role. Saved grants apply to later entries of the key; accounts keep what
    they received.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        games: The games, whose data lists their starting keys.
        store: Where the product keys are kept.
    """
    router = APIRouter()

    async def find(code: str) -> KeyView:
        views = key_views(games, await store.keys(), await store.key_history())
        view = next((view for view in views if view.key.code == code), None)
        if view is None:
            raise HTTPException(404)
        return view

    async def page(
        request: Request, view: KeyView, form: KeyForm, status: int = 200
    ) -> HTMLResponse:
        return await sessions.render(
            request,
            "admin.KeyEdit",
            status,
            admin=sessions.admin(request, Role.ADMIN, SECTION),
            view=view,
            form=form,
            codes=languages(),
            longest=MAXIMUM_LABEL,
        )

    @router.get("/admin/keys/{code}", response_class=HTMLResponse)
    async def edit(request: Request, code: str) -> HTMLResponse:
        sessions.admin(request, Role.ADMIN, SECTION)
        view = await find(code)
        return await page(request, view, KeyForm.of(view))

    @router.post("/admin/keys/{code}")
    async def save(request: Request, code: str) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        sent = await request.form()
        sessions.check(request, sent)
        view = await find(code)
        form = KeyForm.read(sent)
        if form.error:
            return await page(request, view, form, 400)
        key = view.key
        limits = KeyLimits(key.code, int(form.max_uses) if form.max_uses else None, form.once, 0)
        # A starting key keeps its game's texts as labels until the admin writes others.
        labels = {
            language: label
            for language, label in form.labels.items()
            if label and label != view.fallback(language)
        }
        await store.edit_key(key.code, form.grants(), labels, limits, admin)
        sessions.toast(request, "web.admin.toast.key_saved", code=key.code)
        target = localize("/admin/keys", request.state.language)
        return RedirectResponse(f"{target}{KeyList().link(open=key.code)}#key-{key.code}", 303)

    return router
