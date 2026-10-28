from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.account_management import AccountManagement
from openfesl.core.accounts import Account
from openfesl.core.registration import Terms
from openfesl.core.texts import localize
from openfesl.web.account.session import ACCOUNT, SITE, TERMS, account_of
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, toast


class TermsPages:
    """The current terms for an account that accepted an older version, and their acceptance.

    Games refuse the logins of such an account, and some have no screen of their own to accept
    the terms on, so the site offers them after signing in and on the account page.
    """

    def __init__(
        self,
        pages: Pages,
        accounts: Callable[[int], Awaitable[Account | None]],
        management: Callable[[], AccountManagement],
        terms: Terms,
    ) -> None:
        """Prepare the terms' page.

        Args:
            pages: Renders the page.
            accounts: Returns an account by its number.
            management: Returns the account management, once the application has started.
            terms: The terms, in every language they are written in.
        """
        self._pages = pages
        self._accounts = accounts
        self._management = management
        self._terms = terms

    def routes(self) -> APIRouter:
        """Return the terms' page and the form that accepts them."""
        router = APIRouter()

        @router.get(TERMS, response_class=HTMLResponse)
        async def page(request: Request, next: str = "") -> Response:
            back = inside(next, SITE)
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            if account.terms_version == self._terms.version:
                return _onward(request, back)
            return self._render(request, back)

        @router.post(TERMS)
        async def accept(request: Request) -> Response:
            form = await request.form()
            back = inside(str(form.get("next", "")), SITE)
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            if not form_matches(request, form):
                return self._render(request, back, "form")
            if not await self._management().accept_terms(account, str(form.get("version", ""))):
                return self._render(request, back, "changed")
            toast(request, "web.account.toast.terms")
            return _onward(request, back)

        return router

    def _render(self, request: Request, back: str, notice: str = "") -> HTMLResponse:
        return self._pages.render(
            request,
            "account.Terms",
            400 if notice else 200,
            steel=True,
            csrf=form_token(request),
            next=back,
            notice=notice,
            terms=terms_shown(self._terms, request.state.language),
        )


def terms_shown(terms: Terms, language: str) -> dict[str, object]:
    """Return the terms as their acceptance shows them: the version and the text's paragraphs.

    Args:
        terms: The terms.
        language: The page's language code; English where the terms have no translation.
    """
    text = terms.text(terms.language(language))
    return {"version": terms.version, "paragraphs": text.split("\n\n")}


def _onward(request: Request, back: str) -> RedirectResponse:
    return RedirectResponse(back or localize(ACCOUNT, request.state.language), 303)
