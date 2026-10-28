import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import Request

from openfesl.web.pages import Toast


class FormExpired(Exception):
    """Raised for a form whose token does not match the session's; nothing changes."""


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
    token = request.session.get("csrf")
    sent = form.get("csrf")
    if not token or not isinstance(sent, str) or not secrets.compare_digest(token, sent):
        raise FormExpired


def toast(request: Request, key: str, error: bool = False, **values: str) -> None:
    """Have the next page say what came of a change.

    Args:
        request: The change's request.
        key: The text in the catalogues.
        error: Whether the change failed.
        values: The text's placeholders.
    """
    request.session["toast"] = {"key": key, "values": values, "error": error}


def take_toast(request: Request) -> Toast | None:
    """Return what came of the last change, if it is still to be shown, and forget it.

    Args:
        request: The request of the page that shows it.
    """
    shown = request.session.pop("toast", None)
    return Toast(shown["key"], shown["values"], shown["error"]) if shown else None


def notify(request: Request, kind: str, **values: str) -> None:
    """Have the next sign-in page say what happened, such as a session that expired.

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


def inside(path: str, prefixes: tuple[str, ...]) -> str:
    """Return a path when it leads to one of the site's own pages, otherwise an empty string.

    Args:
        path: The path, such as the page that a form names to open next.
        prefixes: What the allowed pages' paths start with, such as /admin/ and /pl/admin/.
    """
    # A browser takes a path with // or a backslash to another site.
    if path.startswith(prefixes) and "//" not in path and "\\" not in path:
        return path
    return ""
