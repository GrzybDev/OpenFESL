from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openfesl.core.accounts import Account
from openfesl.core.identities import Identity
from openfesl.core.sign_in import Refusal, SignInRefused
from openfesl.core.texts import localize
from openfesl.core.verification import VALID_FOR
from openfesl.web.pages import Player
from openfesl.web.sessions import notify, renew_form_token, toast

# A player signs in again after this long, so a browser left signed in does not stay so for good.
LIFETIME = timedelta(days=14)
PLAYER = "player"
# Every page of the site, in any language, which a sign-in may return to.
SITE = ("/",)
# The account that proved to be the visitor's while it waits for its address to be confirmed.
WAITING = "waiting"
# The answer of a service whose identity no account uses yet: the account to link it to after a
# proof, or none for the step that creates one.
PENDING = "pending"
# The page where an account without a display name chooses one before any other of its pages.
DISPLAY_NAME = "/account/display-name"
# The notices' times, which the session keeps as ISO text.
_TIMES = ("sent", "since", "expires", "last", "until")


def start(request: Request, account: Account, way: str) -> None:
    """Sign an account in on the site's pages in this browser.

    Args:
        request: The request that signs it in.
        account: The account.
        way: The name of the service it signed in through; empty for the e-mail address.
    """
    request.session[PLAYER] = {
        "account": account.id,
        "epoch": account.session_epoch,
        "since": datetime.now(UTC).isoformat(),
        "way": way,
    }
    renew_form_token(request)


def landing(request: Request, account: Account, back: str) -> RedirectResponse:
    """Send a browser that has just signed an account in on to the page it is to open.

    An account without a display name, as one made in a game, chooses one first.

    Args:
        request: The request that signed it in.
        account: The account.
        back: The page to open after signing in, if any; the account page when empty.
    """
    if account.display_name is None:
        return open_page(request, DISPLAY_NAME, back)
    return RedirectResponse(back or localize("/account", request.state.language), 303)


def end(request: Request) -> None:
    """Sign the account out of the site's pages in this browser.

    Args:
        request: The request that signs it out.
    """
    request.session.pop(PLAYER, None)
    renew_form_token(request)


def signed_in(request: Request) -> Player | None:
    """Return the account signed in on the site's pages with a request, if any.

    Args:
        request: The request.
    """
    player: Player | None = request.scope.get("state", {}).get("player")
    return player


def refused_notice(request: Request, refused: SignInRefused, service: str = "") -> dict[str, str]:
    """Return what the sign-in page says about a refused sign-in, with its times as ISO text.

    An account that waits for its address to be confirmed is remembered, so that the page's form
    can send it a new link.

    Args:
        request: The sign-in's request.
        refused: The refusal.
        service: The name of the service it went through; empty for the e-mail address.
    """
    account = refused.account
    match refused.refusal:
        case Refusal.UNCONFIRMED if account and account.verification_expires_at:
            request.session[WAITING] = account.id
            sent = (account.verification_expires_at - VALID_FOR).isoformat()
            return {
                "kind": "unconfirmed",
                "address": account.nuid,
                "sent": sent,
                "service": service,
            }
        case Refusal.DEACTIVATED if account and account.state_changed:
            return {"kind": "deactivated", "since": account.state_changed.isoformat()}
        case Refusal.PAUSED:
            return {"kind": "paused"}
    return {"kind": "wrong"}


def pending_identity(request: Request) -> tuple[Identity, int] | None:
    """Return the service's answer that waits for a proof, with the account it is to be linked to.

    Args:
        request: The request of the proof.
    """
    pending = request.session.get(PENDING)
    if not pending or pending.get("account") is None:
        return None
    identity = Identity(pending["service"], pending["subject"], pending["name"], pending["address"])
    return identity, pending["account"]


def proved(
    request: Request, account: Account, identity: Identity, service: str, linked: bool
) -> RedirectResponse:
    """Sign in an account that a proof showed to be the visitor's, saying what was linked.

    Args:
        request: The request that ends the proof.
        account: The account.
        identity: The identity the proof was for.
        service: The name of its service.
        linked: Whether the identity was linked to the account.
    """
    back = (request.session.pop(PENDING, None) or {}).get("next", "")
    request.session.pop(WAITING, None)
    start(request, account, way=service)
    name = account.display_name or account.nuid
    if linked:
        toast(
            request, "web.account.toast.linked", service=service, name=identity.name, account=name
        )
    else:
        toast(request, "web.account.toast.signed_in", account=name)
    return landing(request, account, back)


def shown(notice: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return a page's notice with its times as moments, as the page shows them.

    Args:
        notice: The notice, whose times may be ISO text, as from the session.
    """
    if not notice:
        return None
    return {
        key: datetime.fromisoformat(value) if key in _TIMES and isinstance(value, str) else value
        for key, value in notice.items()
    }


def to_sign_in(request: Request, back: str, kind: str, **values: str) -> RedirectResponse:
    """Return to the sign-in page, which says what happened.

    Args:
        request: The request that ends there.
        back: The page to open after signing in, if any.
        kind: What happened, such as failed.
        values: The details the page names, such as the service.
    """
    notify(request, kind, **values)
    return open_page(request, "/sign-in", back)


def open_page(request: Request, path: str, back: str = "") -> RedirectResponse:
    """Send the browser to one of the sign-in's pages in the request's language.

    Args:
        request: The request.
        path: The page's path without a language, such as /sign-in.
        back: The page to open after signing in, which the page carries on; none when empty.
    """
    target = localize(path, request.state.language)
    return RedirectResponse(f"{target}?{urlencode({'next': back})}" if back else target, 303)


class PlayerSessionMiddleware:
    """Recognises the account signed in with a request while its session holds.

    A session ends once it is older than its lifetime, or once the account's session epoch moved
    on, as a new password or state does; it then ends at the next page.
    """

    def __init__(self, app: ASGIApp, accounts: Callable[[int], Awaitable[Account | None]]) -> None:
        """Recognise the accounts that the sessions name.

        Args:
            app: The application inside the session middleware.
            accounts: Returns an account by its number.
        """
        self._app = app
        self._accounts = accounts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer a request with the signed-in account in its state.

        Args:
            scope: The ASGI connection scope.
            receive: Reads the request's messages.
            send: Writes the response's messages.
        """
        # The static files need no account, and are fetched with every page.
        if scope["type"] == "http" and not scope["path"].startswith("/static/"):
            await self._recognise(scope)
        await self._app(scope, receive, send)

    async def _recognise(self, scope: Scope) -> None:
        session = scope.get("session")
        kept = session.get(PLAYER) if session else None
        if not kept:
            return
        account = await self._accounts(kept["account"])
        expired = datetime.now(UTC) - datetime.fromisoformat(kept["since"]) > LIFETIME
        if account is None or account.session_epoch != kept["epoch"] or expired:
            request = Request(scope)
            request.session.pop(PLAYER)
            if expired:
                notify(request, "expired")
            return
        scope.setdefault("state", {})["player"] = Player(
            account.id, account.display_name, kept["way"]
        )
