from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.account_management import AccountManagement, AccountOverview
from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.limits import Wait
from openfesl.core.policies import GamePolicies
from openfesl.core.problems import Problem
from openfesl.core.registration import Terms, countries
from openfesl.core.sign_in import LinkSent, WebSignIn
from openfesl.core.texts import localize, translate
from openfesl.core.verification import VALID_FOR
from openfesl.web.account.fields import field_errors
from openfesl.web.account.session import DISPLAY_NAME, open_page, shown, signed_in
from openfesl.web.account.sign_in import password_games
from openfesl.web.pages import Pages, Toast
from openfesl.web.sessions import form_matches, form_token, notify, toast
from openfesl.web.turnstile import Turnstile

ACCOUNT = "/account"
# What the account page's last form left for one of its parts to say, until the page shows it.
FORM = "account_form"


def answered(
    request: Request, part: str, problems: Sequence[Problem] = (), **said: str | bool
) -> RedirectResponse:
    """Return to a part of the account page, which says what came of the form sent from it.

    Where scripts run, only that part is swapped in place; without them the page opens at it.

    Args:
        request: The form's request.
        part: The part's id, such as profile.
        problems: What is wrong with the values the form sent.
        said: What else the part says, such as which form it was and the value it sent; nothing
            for a saved change, which a toast reports.
    """
    if problems or said:
        kept = [[problem.field, problem.kind, problem.detail] for problem in problems]
        request.session[FORM] = {"part": part, "problems": kept, **said}
    return RedirectResponse(f"{localize(ACCOUNT, request.state.language)}#{part}", 303)


async def account_of(
    request: Request, accounts: Callable[[int], Awaitable[Account | None]]
) -> Account | RedirectResponse:
    """Return the account signed in with a request, or the page that has to come first.

    A visitor who is not signed in signs in and comes back to the account page; an account
    without a display name chooses one first.

    Args:
        request: The request of one of the account page's pages or forms.
        accounts: Returns an account by its number.
    """
    player = signed_in(request)
    account = await accounts(player.account_id) if player else None
    if account is None:
        notify(request, "needed", page="account")
        return open_page(request, "/sign-in", localize(ACCOUNT, request.state.language))
    if account.display_name is None:
        return open_page(request, DISPLAY_NAME)
    return account


class AccountPages:
    """The account page, where a signed-in player sees their account and changes it."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        accounts: Callable[[int], Awaitable[Account | None]],
        management: Callable[[], AccountManagement],
        sign_in: Callable[[], WebSignIn],
        policies: GamePolicies,
        terms: Terms,
        mail: bool,
        turnstile: Turnstile | None,
    ) -> None:
        """Prepare the account page.

        Args:
            pages: Renders the page.
            games: The games, which the page names where the account was made and which games
                ask for its address.
            accounts: Returns an account by its number.
            management: Returns the account management, once the application has started.
            sign_in: Returns the site's sign-in, which sends new confirmation links.
            policies: The site's and each game's policies, which say whether an account waits
                for its confirmation link.
            terms: The terms, whose current version the page says the account accepted or not.
            mail: Whether the server sends mail, which a new confirmation link needs.
            turnstile: Cloudflare's check of the form that sends a new link; None when the
                deployment sets none up.
        """
        self._pages = pages
        self._games = games
        self._accounts = accounts
        self._management = management
        self._sign_in = sign_in
        self._policies = policies
        self._terms = terms
        self._mail = mail
        self._turnstile = turnstile

    def routes(self) -> APIRouter:
        """Return the account page and the forms of its profile."""
        router = APIRouter()

        @router.get(ACCOUNT, response_class=HTMLResponse)
        async def page(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            overview = await self._management().overview(account.id)
            if overview is None:
                return open_page(request, "/sign-in")
            return await self._render(request, overview, request.session.pop(FORM, None))

        @router.post(ACCOUNT + "/country")
        async def change_country(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            form = await request.form()
            if not form_matches(request, form):
                return answered(request, "profile", form="country", notice="form")
            country = str(form.get("country", ""))
            if problem := await self._management().change_country(account, country):
                return answered(request, "profile", [problem], form="country")
            name = dict(countries(request.state.language))[country.upper()]
            toast(request, "web.account.toast.country", country=name)
            return answered(request, "profile")

        @router.post(ACCOUNT + "/confirmation")
        async def confirmation(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            form = await request.form()
            if not form_matches(request, form):
                return answered(request, "profile", form="confirmation", notice="form")
            if self._turnstile and not await self._turnstile.passed(request, form):
                return answered(request, "profile", form="confirmation", notice="check")
            match await self._sign_in().send_confirmation(account.id, request.state.language):
                case LinkSent(address, expires):
                    return answered(
                        request,
                        "profile",
                        form="confirmation",
                        notice="link_sent",
                        address=address,
                        expires=expires.isoformat(),
                    )
                case Wait(last, until):
                    return answered(
                        request,
                        "profile",
                        form="confirmation",
                        notice="link_wait",
                        last=last.isoformat(),
                        until=until.isoformat(),
                    )
            return answered(request, "profile")

        return router

    async def _render(
        self, request: Request, overview: AccountOverview, said: dict[str, Any] | None
    ) -> HTMLResponse:
        language = request.state.language
        account = overview.account
        made_in = self._games.by_id(account.made_in or "")
        said = shown(said) or {}
        problems = [Problem(*problem) for problem in said.get("problems", [])]
        waiting = account.verification_expires_at
        page = self._pages.render(
            request,
            "account.Account",
            steel=True,
            csrf=form_token(request),
            overview=overview,
            made_in=made_in,
            said=said,
            errors=field_errors(problems, language),
            countries=countries(language),
            sent=waiting - VALID_FOR if waiting else None,
            waits=bool(waiting) and await self._policies.confirms(made_in),
            mail=self._mail,
            check=self._turnstile.site_key if self._turnstile and waiting and self._mail else "",
            accepted=account.terms_version == self._terms.version,
            password_games=password_games(self._games, language),
            personas=[translate(f"games.{game.id}.personas", language) for game in self._games],
            failed=Toast("web.toast.failed", {}, error=True),
        )
        if self._turnstile:
            self._turnstile.allow(page)
        return page
