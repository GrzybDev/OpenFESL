import asyncio
import secrets
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from weakref import WeakValueDictionary

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.datastructures import FormData
from starlette.exceptions import HTTPException

# A signed-in admin signs in again after this long, as a stolen cookie should not last.
ADMIN_LIFETIME = timedelta(hours=12)


class FormExpired(Exception):
    """Raised for a form whose token does not match the session's; nothing changes."""


def admin_signed_in(request: Request) -> bool:
    """Return whether an admin is signed in to the admin panel with a request, while it holds.

    Args:
        request: The request.
    """
    session = request.scope.get("session") or {}
    signed_in = session.get("admin")
    since = datetime.fromisoformat(signed_in["since"]) if signed_in else None
    return since is not None and datetime.now(UTC) - since <= ADMIN_LIFETIME


def form_token(request: Request) -> str:
    """Return the token that a page's forms send back, issuing the session one when it has none.

    Args:
        request: The request the page answers.
    """
    token: str = request.session.setdefault("csrf", secrets.token_urlsafe(32))
    return token


def renew_form_token(request: Request) -> None:
    """Give the session a new form token, so that a token seen before is no use.

    Args:
        request: The request that signs someone in or out.
    """
    request.session["csrf"] = secrets.token_urlsafe(32)


def check_form(request: Request, form: Mapping[str, Any]) -> None:
    """Check that a form came from a page of this session.

    Args:
        request: The form's request.
        form: The form's fields.

    Raises:
        FormExpired: When its token is not the session's.
    """
    if not form_matches(request, form):
        raise FormExpired


async def checked_form(request: Request) -> FormData:
    """Return a request's form once it proves to come from a page of this session.

    Args:
        request: The request that sent the form.

    Raises:
        FormExpired: For a form from elsewhere or from a token renewed since.
    """
    form = await request.form()
    check_form(request, form)
    return form


def form_matches(request: Request, form: Mapping[str, Any]) -> bool:
    """Return whether a form came from a page of this session.

    Args:
        request: The form's request.
        form: The form's fields.
    """
    token = request.session.get("csrf")
    sent = form.get("csrf")
    if not token or not isinstance(sent, str):
        return False
    # As bytes, since compare_digest refuses text that is not ASCII.
    return secrets.compare_digest(token.encode(), sent.encode())


# How many purchase forms that bought the server remembers, forgetting the oldest first: a copy
# that a double click or Back sends again comes well within that many purchases.
SENT_KEPT = 10_000


def once_token() -> str:
    """Return a new token for a purchase form, by which SentForms lets the form buy once."""
    return secrets.token_urlsafe(16)


def purchase_matches(request: Request, form: Mapping[str, Any]) -> bool:
    """Return whether a purchase form came from a page of this session with its one-time token.

    A form without the token counts as expired, so that such forms never share one token.

    Args:
        request: The form's request.
        form: The form's fields.
    """
    return form_matches(request, form) and bool(form.get("once"))


class SentForms:
    """The purchase forms that bought, so that a form sent again buys nothing more.

    The server keeps them rather than the session: a double click sends its second copy with the
    first one's session cookie, before the first one's answer arrives.
    """

    def __init__(self) -> None:
        """Remember no form yet."""
        self._bought: dict[str, None] = {}
        self._sending: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    @asynccontextmanager
    async def once(self, form: Mapping[str, Any]) -> AsyncIterator[bool]:
        """Hold a form's purchase until it ends, after any copy of the form sent earlier.

        A purchase that leaves the block without an exception counts as made.

        Args:
            form: The form's fields, with the token that once_token gave it.

        Yields:
            Whether a copy of the form bought already, so that nothing is to be bought.
        """
        token = str(form.get("once", ""))
        # Copies of one form wait for each other; the lock goes once no copy holds it.
        lock = self._sending.setdefault(token, asyncio.Lock())
        async with lock:
            yield token in self._bought
            self._bought[token] = None
            if len(self._bought) > SENT_KEPT:
                del self._bought[next(iter(self._bought))]


def toast(request: Request, key: str, error: bool = False, **values: str) -> None:
    """Have the next page say what came of a change.

    Args:
        request: The change's request.
        key: The text in the catalogues.
        error: Whether the change failed.
        values: The text's placeholders, and what else the next page marks as changed.
    """
    request.session["toast"] = {"key": key, "values": values, "error": error}


def toast_at(
    request: Request, page: str, key: str, /, error: bool = False, **values: str
) -> RedirectResponse:
    """Send the browser to a page that says what came of a change.

    Args:
        request: The request that made the change.
        page: The page's address.
        key: The text's key.
        error: Whether the change failed.
        values: What the text names.
    """
    toast(request, key, error, **values)
    return RedirectResponse(page, 303)


def or_404[T](found: T | None) -> T:
    """Return a value an address found, or answer 404 when there is none.

    Args:
        found: What the address found.

    Raises:
        HTTPException: 404 when it found nothing.
    """
    if found is None:
        raise HTTPException(404)
    return found


def notify(request: Request, kind: str, **values: str) -> None:
    """Have the next sign-in page say what happened, such as a session that expired.

    The admin panel's and the site's sign-in pages share it, as one browser signs in to one of
    them at a time.

    Args:
        request: The request in which it happened.
        kind: What happened, such as expired.
        values: The details the page names, such as the account refused.
    """
    request.session["notice"] = {"kind": kind, **values}


def take_notice(request: Request) -> dict[str, str] | None:
    """Return what the sign-in page is to say, if anything, and forget it.

    Args:
        request: The request of the sign-in page.
    """
    notice: dict[str, str] | None = request.session.pop("notice", None)
    return notice


# What takes a browser from a path to another site: // and a backslash, and tabs and line breaks,
# which it drops from an address before it reads the address, so that /<tab>/site is //site.
_OFFSITE = ("//", "\\", "\t", "\r", "\n")


def inside(path: str, prefixes: tuple[str, ...]) -> str:
    """Return a path when it leads to one of the site's own pages, otherwise an empty string.

    Args:
        path: The path, such as the page that a form names to open next.
        prefixes: What the allowed pages' paths start with, such as /admin/ and /pl/admin/.
    """
    if path.startswith(prefixes) and not any(mark in path for mark in _OFFSITE):
        return path
    return ""
