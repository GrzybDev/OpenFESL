from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.account_management import AccountManagement, AccountOverview
from openfesl.core.accounts import Account
from openfesl.core.texts import localize, translate, translate_count
from openfesl.web.account.fields import field_errors
from openfesl.web.account.services import SignInServices
from openfesl.web.account.session import ACCOUNT, account_of, answered, end
from openfesl.web.pages import Pages, listed
from openfesl.web.sessions import form_matches

DELETION = ACCOUNT + "/deletion"
DONE = DELETION + "/done"
# The deactivated account that the page after a deletion request names, until it is shown.
DEACTIVATED = "deactivated"


class DeletionPages:
    """The deletion request: the question, which asks for the display name, and the page after."""

    def __init__(
        self,
        pages: Pages,
        accounts: Callable[[int], Awaitable[Account | None]],
        management: Callable[[], AccountManagement],
        services: SignInServices,
        apply: Callable[[], Awaitable[None]],
    ) -> None:
        """Prepare the deletion request's pages.

        Args:
            pages: Renders the pages.
            accounts: Returns an account by its number.
            management: Returns the account management, once the application has started.
            services: The sign-in services, which the question names among the ways in.
            apply: Applies the recorded changes to the running games at once, which sign the
                deactivated account out.
        """
        self._pages = pages
        self._accounts = accounts
        self._management = management
        self._services = services
        self._apply = apply

    def routes(self) -> APIRouter:
        """Return the deletion request's question, its form and the page after it."""
        router = APIRouter()

        @router.get(DELETION, response_class=HTMLResponse)
        async def question(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            return await self._question(request, account)

        @router.post(DELETION)
        async def request_deletion(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            form = await request.form()
            if not form_matches(request, form) or form.get("confirmed") != "1":
                return answered(request, "deletion", notice="form")
            name = str(form.get("confirm_name", "")).strip()
            language = request.state.language
            deactivated = await self._management().request_deletion(account, name, language)
            if not isinstance(deactivated, datetime):
                errors = field_errors([deactivated], language)
                return await self._question(request, account, name, errors["confirm_name"])
            await self._apply()
            end(request)
            request.session[DEACTIVATED] = {"name": name, "since": deactivated.isoformat()}
            return RedirectResponse(localize(DONE, language), 303)

        @router.get(DONE, response_class=HTMLResponse)
        async def done(request: Request) -> Response:
            deactivated = request.session.pop(DEACTIVATED, None)
            if deactivated is None:
                return RedirectResponse(localize("/", request.state.language), 303)
            since = datetime.fromisoformat(deactivated["since"])
            return self._pages.render(
                request,
                "account.Deactivated",
                steel=True,
                switch=False,
                name=deactivated["name"],
                since=since,
            )

        return router

    async def _question(
        self, request: Request, account: Account, value: str = "", error: str = ""
    ) -> HTMLResponse:
        overview = await self._management().overview(account.id)
        assert overview is not None
        language = request.state.language
        return self._pages.render(
            request,
            "account.Decision",
            400 if error else 200,
            steel=True,
            name=account.display_name,
            question=translate("web.account.deletion.question", language)
            % {"name": account.display_name},
            text=translate("web.account.deletion.question.text", language),
            action=localize(DELETION, language),
            label=translate("web.account.deletion.action", language),
            busy=translate("web.account.deletion.busy", language),
            back=f"{localize(ACCOUNT, language)}#deletion",
            facts=self._facts(overview, language),
            confirm=account.display_name,
            value=value,
            error=error,
        )

    def _facts(self, overview: AccountOverview, language: str) -> list[tuple[str, str]]:
        # What the account holds: its personas in each game it played, and its ways in.
        facts = []
        for entry in overview.games:
            if count := len(entry.personas):
                text = translate_count(f"games.{entry.game.id}.personas.count", count, language)
                facts.append((entry.game.name, text % {"count": count}))
        ways = [translate("web.account.password", language)] if overview.password else []
        for identity in overview.linked:
            ways.append(self._services.name_of(identity.service, language))
        facts.append((translate("web.account.ways.title", language), listed(ways, language)))
        return facts
