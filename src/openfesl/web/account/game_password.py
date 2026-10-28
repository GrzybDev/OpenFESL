from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.account_management import AccountManagement, AccountOverview
from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.texts import localize, translate
from openfesl.web.account.page import ACCOUNT, account_of, answered
from openfesl.web.account.services import SignInServices
from openfesl.web.account.session import renew
from openfesl.web.pages import Pages, listed
from openfesl.web.sessions import form_matches, toast

PASSWORD = ACCOUNT + "/game-password"
REMOVE = PASSWORD + "/remove"


class GamePasswordForms:
    """The account page's game password: set or changed in its fold, removed after a question."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        accounts: Callable[[int], Awaitable[Account | None]],
        management: Callable[[], AccountManagement],
        services: SignInServices,
    ) -> None:
        """Prepare the game password's forms.

        Args:
            pages: Renders the question before the removal.
            games: The games, which the texts name as those that log in with the game password.
            accounts: Returns an account by its number.
            management: Returns the account management, once the application has started.
            services: The sign-in services, which the texts name as the ways in that stay.
        """
        self._pages = pages
        self._games = games
        self._accounts = accounts
        self._management = management
        self._services = services

    def routes(self) -> APIRouter:
        """Return the form that sets or changes the game password and the removal's question."""
        router = APIRouter()

        @router.post(PASSWORD)
        async def change(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            form = await request.form()
            if not form_matches(request, form):
                return answered(request, "sign-in", form="password", notice="form")
            # The change form asks for the current game password; the one that sets it does not.
            changing = "current_password" in form
            problems = await self._management().change_password(
                account, str(form.get("current_password", "")), str(form.get("password", ""))
            )
            if problems:
                return answered(request, "sign-in", problems, form="password")
            await self._keep_signed_in(request, account)
            if changing:
                toast(request, "web.account.toast.password_changed")
            else:
                games = self._names(request)
                toast(request, "web.account.toast.password_set", games=games, address=account.nuid)
            return answered(request, "sign-in")

        @router.get(REMOVE, response_class=HTMLResponse)
        async def question(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            overview = await self._management().overview(account.id)
            if overview is None or not overview.password or overview.ways_in < 2:
                return answered(request, "sign-in")
            language = request.state.language
            return self._pages.render(
                request,
                "account.Decision",
                steel=True,
                name=account.display_name,
                question=translate("web.account.password.remove.question", language),
                text=translate("web.account.password.remove.text", language)
                % {"games": self._names(request), "services": self._ways(overview, language)},
                action=localize(REMOVE, language),
                label=translate("web.account.password.remove.action", language),
                busy=translate("web.account.password.remove.busy", language),
                back=f"{localize(ACCOUNT, language)}#sign-in",
            )

        @router.post(REMOVE)
        async def remove(request: Request) -> Response:
            account = await account_of(request, self._accounts)
            if not isinstance(account, Account):
                return account
            form = await request.form()
            if not form_matches(request, form) or form.get("confirmed") != "1":
                return answered(request, "sign-in", form="password", notice="form")
            management = self._management()
            if not await management.remove_password(account):
                return answered(request, "sign-in", form="password", notice="last")
            await self._keep_signed_in(request, account)
            overview = await management.overview(account.id)
            language = request.state.language
            services = self._ways(overview, language)
            toast(request, "web.account.toast.password_removed", services=services)
            return RedirectResponse(f"{localize(ACCOUNT, language)}#sign-in", 303)

        return router

    async def _keep_signed_in(self, request: Request, account: Account) -> None:
        # The change ended the account's web sessions; this browser made it and stays signed in.
        changed = await self._accounts(account.id)
        if changed is not None:
            renew(request, changed.session_epoch)

    def _names(self, request: Request) -> str:
        # Every game logs in with the game password, in the game or through the launcher.
        language = request.state.language
        return listed(
            [translate(f"games.{game.id}.short", language) for game in self._games], language
        )

    def _ways(self, overview: AccountOverview | None, language: str) -> str:
        # The linked services that sign in, which only the configured ones do.
        linked = overview.linked if overview else []
        views = [self._services.view(entry.service, language) for entry in linked]
        return listed([view.name for view in views if view], language)
