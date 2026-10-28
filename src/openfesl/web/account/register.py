from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.limits import Wait
from openfesl.core.policies import GamePolicies
from openfesl.core.problems import Problem
from openfesl.core.registration import countries, is_country
from openfesl.core.texts import localize
from openfesl.core.verification import PATH, Confirmation
from openfesl.core.web_registration import (
    Mailed,
    Registration,
    TooYoung,
    WebRegistration,
)
from openfesl.web.account.fields import birth_order, field_errors
from openfesl.web.account.message import SECRET, message_page
from openfesl.web.account.session import SITE, signed_in, start
from openfesl.web.account.sign_in import password_games
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_matches, form_token, inside, toast
from openfesl.web.turnstile import Turnstile

# The form's fields that come back with its page, all but the game password.
_KEPT = ("email", "display_name", "country", "birth_day", "birth_month", "birth_year")


class RegisterPages:
    """The site's registration with an e-mail address, under the site's confirmation policy.

    The link that confirms an address opens here too, whether the site or a game sent it.
    """

    def __init__(
        self,
        pages: Pages,
        games: Games,
        policies: GamePolicies,
        registration: Callable[[], WebRegistration],
        mail: bool,
        turnstile: Turnstile | None,
    ) -> None:
        """Prepare the registration's pages.

        Args:
            pages: Renders the pages.
            games: The games, which the pages name where they explain accounts.
            policies: The site's policy, which says whether a new account waits for its link.
            registration: Returns the registration, once the application has started.
            mail: Whether the server sends mail.
            turnstile: Cloudflare's check of the forms; None when the deployment sets none up.
        """
        self._pages = pages
        self._games = games
        self._policies = policies
        self._registration = registration
        self._mail = mail
        self._turnstile = turnstile

    def routes(self) -> APIRouter:
        """Return the registration's page, its form and the page that confirms addresses."""
        router = APIRouter()

        @router.get("/register", response_class=HTMLResponse)
        async def page(request: Request, next: str = "") -> Response:
            back = inside(next, SITE)
            if signed_in(request):
                return RedirectResponse(back or _account(request), 303)
            if not await self._registration().offered():
                return self._unavailable(request)
            return await self._form(request, back, {"country": _reported(request)})

        @router.post("/register")
        async def register(request: Request) -> Response:
            return await self._register(request)

        # Opening the link only asks, as mail scanners open links too; its button confirms.
        @router.get(PATH, response_class=HTMLResponse)
        async def open_link(request: Request, token: str) -> HTMLResponse:
            return self._link_page(request, token, await self._registration().find(token))

        @router.post(PATH)
        async def confirm(request: Request, token: str) -> HTMLResponse:
            registration = self._registration()
            if not form_matches(request, await request.form()):
                return self._link_page(request, token, await registration.find(token), "form")
            confirmed = await registration.confirm(token)
            if isinstance(confirmed, Confirmation):
                return self._link_page(request, token, confirmed)
            key = "web.account.confirm.done" + ("" if confirmed.waited else ".already")
            return message_page(self._pages, request, 200, key, _onward(request), headers=SECRET)

        return router

    async def _register(self, request: Request) -> Response:
        form = await request.form()
        back = inside(str(form.get("next", "")), SITE)
        values: dict[str, Any] = {name: str(form.get(name, "")).strip() for name in _KEPT}
        values["terms"] = form.get("terms") == "1"
        if not form_matches(request, form):
            return await self._form(request, back, values, notice="form", status=400)
        registration = self._registration()
        if not await registration.offered():
            return self._unavailable(request)
        if self._turnstile and not await self._turnstile.passed(request, form):
            return await self._form(request, back, values, notice="check", status=400)
        entry = Registration(
            values["email"],
            str(form.get("password", "")),
            values["display_name"],
            values["country"],
            (values["birth_day"], values["birth_month"], values["birth_year"]),
            values["terms"],
        )
        match await registration.register(entry, request.state.language):
            case list() as problems:
                return await self._form(request, back, values, problems, status=400)
            case TooYoung(age, consent):
                key = "web.account.register.young" + (".consent" if consent else "")
                country = dict(countries(request.state.language)).get(entry.country.upper(), "")
                home = ("/", "web.home")
                return message_page(self._pages, request, 403, key, home, age=age, country=country)
            case Wait():
                return await self._form(request, back, values, notice="paused", status=429)
            case Mailed(address):
                rows = ("web.account.register.sent.in_use", "web.account.register.sent.none")
                sign_in = ("/sign-in", "web.account.sign_in")
                return message_page(
                    self._pages,
                    request,
                    200,
                    "web.account.register.sent",
                    sign_in,
                    rows,
                    address=address,
                )
            case Account() as account:
                start(request, account, way="")
                toast(request, "web.account.toast.created")
                return RedirectResponse(back or _account(request), 303)

    async def _form(
        self,
        request: Request,
        back: str,
        values: Mapping[str, Any],
        problems: list[Problem] | None = None,
        notice: str = "",
        status: int = 200,
    ) -> HTMLResponse:
        language = request.state.language
        page = self._pages.render(
            request,
            "account.Register",
            status,
            steel=True,
            csrf=form_token(request),
            next=back,
            values={"terms": False, **dict.fromkeys(_KEPT, ""), **values},
            errors=field_errors(problems or [], language),
            notice=notice,
            countries=countries(language),
            order=birth_order(language),
            reported=values.get("country", "") == _reported(request) != "",
            check=self._turnstile.site_key if self._turnstile else "",
            confirm=await self._policies.site_confirmation(),
            mail=self._mail,
            password_games=password_games(self._games, language),
        )
        if self._turnstile:
            self._turnstile.allow(page)
        return page

    def _link_page(
        self, request: Request, token: str, found: Account | Confirmation, notice: str = ""
    ) -> HTMLResponse:
        if isinstance(found, Confirmation):
            status = 410 if found is Confirmation.EXPIRED else 404
            key = f"web.account.confirm.{found.name.lower()}"
            onward = _onward(request)
            return message_page(self._pages, request, status, key, onward, headers=SECRET)
        return self._pages.render(
            request,
            "account.ConfirmLink",
            400 if notice else 200,
            steel=True,
            switch=False,
            headers=SECRET,
            csrf=form_token(request),
            action=localize(PATH.format(token=token), request.state.language),
            account=found.display_name or found.nuid,
            notice=notice,
        )

    def _unavailable(self, request: Request) -> HTMLResponse:
        sign_in = ("/sign-in", "web.account.sign_in")
        key = "web.account.register.unavailable"
        return message_page(self._pages, request, 200, key, sign_in)


def _reported(request: Request) -> str:
    # The country that Cloudflare reports for the connection, only ever a suggestion.
    code = request.headers.get("cf-ipcountry", "").upper()
    return code if is_country(code) else ""


def _onward(request: Request) -> tuple[str, str]:
    # Where a confirmation link's pages lead: the account signed in here, or the sign-in.
    if signed_in(request):
        return "/account", "web.account.confirm.open"
    return "/sign-in", "web.account.sign_in"


def _account(request: Request) -> str:
    return localize("/account", request.state.language)
