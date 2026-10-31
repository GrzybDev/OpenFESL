from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.games import Games
from openfesl.core.identities import Identity
from openfesl.core.limits import Wait
from openfesl.core.policies import GamePolicies
from openfesl.core.problems import Problem
from openfesl.core.registration import countries, is_country
from openfesl.core.texts import localize
from openfesl.core.verification import PATH, Confirmation
from openfesl.core.web_registration import (
    IdentityTaken,
    Mailed,
    Registration,
    TooYoung,
    WebRegistration,
)
from openfesl.web.account.fields import birth_order, field_errors
from openfesl.web.account.message import SECRET, message_page
from openfesl.web.account.services import ServiceView, SignInServices
from openfesl.web.account.session import PENDING, SITE, open_page, signed_in, start, to_sign_in
from openfesl.web.account.sign_in import password_games
from openfesl.web.pages import Pages, listed
from openfesl.web.referral import REFERRAL, ReferralLinks
from openfesl.web.sessions import form_matches, form_token, inside, toast
from openfesl.web.turnstile import Turnstile

SERVICE = "/register/service"
# The form's fields that come back with its page, all but the game password.
_KEPT = ("email", "display_name", "country", "birth_day", "birth_month", "birth_year")


class RegisterPages:
    """The site's registration with an e-mail address, and the step after a service's new identity.

    The step after a service creates the account that the service's identity becomes a way in to.
    Both follow the site's confirmation policy, and the link that confirms an address opens here.
    """

    def __init__(
        self,
        pages: Pages,
        games: Games,
        policies: GamePolicies,
        services: SignInServices,
        registration: Callable[[], WebRegistration],
        mail: bool,
        turnstile: Turnstile | None,
        links: ReferralLinks,
    ) -> None:
        """Prepare the registration's pages.

        Args:
            pages: Renders the pages.
            games: The games, which the pages name where they explain accounts.
            policies: The site's policy, which says whether a new account waits for its link.
            services: The sign-in services, whose new identities the step after a service is for.
            registration: Returns the registration, once the application has started.
            mail: Whether the server sends mail.
            turnstile: Cloudflare's check of the forms; None when the deployment sets none up.
            links: Tell whose personal link of Recruit a Friend a visitor arrived through, which
                the new account becomes the recruit of.
        """
        self._pages = pages
        self._games = games
        self._policies = policies
        self._services = services
        self._registration = registration
        self._mail = mail
        self._turnstile = turnstile
        self._links = links

    def routes(self) -> APIRouter:
        """Return the registration's pages, their forms and the page that confirms addresses."""
        router = APIRouter()

        @router.get("/register", response_class=HTMLResponse)
        async def page(request: Request, next: str = "") -> Response:
            back = inside(next, SITE)
            if signed_in(request):
                return RedirectResponse(back or _account(request), 303)
            if not await self._registration().offered():
                return self._unavailable(request, None)
            return await self._form(request, back, None, {"country": _reported(request)})

        @router.post("/register")
        async def register(request: Request) -> Response:
            return await self._register(request, None, "")

        @router.get(SERVICE, response_class=HTMLResponse)
        async def service_page(request: Request) -> Response:
            pending = _new_identity(request)
            if pending is None:
                return open_page(request, "/sign-in")
            identity, back = pending
            registration = self._registration()
            if not await registration.offered():
                request.session.pop(PENDING, None)
                return self._unavailable(request, identity)
            values = {
                "email": identity.address or "",
                "display_name": await registration.suggested_name(identity.name),
                "country": _reported(request),
            }
            return await self._form(request, back, identity, values)

        @router.post(SERVICE)
        async def register_service(request: Request) -> Response:
            pending = _new_identity(request)
            if pending is None:
                return open_page(request, "/sign-in")
            return await self._register(request, *pending)

        @router.post(SERVICE + "/cancel")
        async def cancel(request: Request) -> Response:
            if form_matches(request, await request.form()):
                request.session.pop(PENDING, None)
            return open_page(request, "/sign-in")

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
            language = request.state.language
            view = self._services.view(confirmed.service or "", language)
            rows: tuple[str, ...] = ("web.account.confirm.service",) if view else ()
            if view and not confirmed.password:
                rows += ("web.account.confirm.password",)
            games = listed(password_games(self._games, language), language)
            return message_page(
                self._pages,
                request,
                200,
                key,
                _onward(request),
                rows,
                SECRET,
                service=view.name if view else "",
                games=games,
            )

        return router

    async def _register(self, request: Request, identity: Identity | None, back: str) -> Response:
        form = await request.form()
        if identity is None:
            back = inside(str(form.get("next", "")), SITE)
        values: dict[str, Any] = {name: str(form.get(name, "")).strip() for name in _KEPT}
        values["terms"] = form.get("terms") == "1"
        if not form_matches(request, form):
            return await self._form(request, back, identity, values, notice="form", status=400)
        registration = self._registration()
        if not await registration.offered():
            return self._unavailable(request, identity)
        if self._turnstile and not await self._turnstile.passed(request, form):
            return await self._form(request, back, identity, values, notice="check", status=400)
        entry = Registration(
            values["email"],
            str(form.get("password", "")),
            values["display_name"],
            values["country"],
            (values["birth_day"], values["birth_month"], values["birth_year"]),
            values["terms"],
        )
        referrer = await self._links.referrer(request)
        recruited_by = (referrer.game.id, referrer.account.id) if referrer else None
        match await registration.register(entry, identity, request.state.language, recruited_by):
            case list() as problems:
                return await self._form(request, back, identity, values, problems, status=400)
            case TooYoung(age, consent):
                key = "web.account.register.young" + (".consent" if consent else "")
                country = dict(countries(request.state.language)).get(entry.country.upper(), "")
                home = ("/", "web.home")
                return message_page(self._pages, request, 403, key, home, age=age, country=country)
            case Wait():
                return await self._form(
                    request, back, identity, values, notice="paused", status=429
                )
            case IdentityTaken():
                # Signing in through the service opens the account the identity now belongs to.
                request.session.pop(PENDING, None)
                service = self._service(request, identity)
                name = service.name if service else ""
                return to_sign_in(request, back, "taken", service=name)
            case Mailed(address):
                request.session.pop(PENDING, None)
                request.session.pop(REFERRAL, None)
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
                request.session.pop(PENDING, None)
                request.session.pop(REFERRAL, None)
                service = self._service(request, identity)
                start(request, account, way=service.name if service else "")
                toast(request, "web.account.toast.created")
                return RedirectResponse(back or _account(request), 303)

    async def _form(
        self,
        request: Request,
        back: str,
        identity: Identity | None,
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
            service=self._service(request, identity),
            identity=identity,
            countries=countries(language),
            order=birth_order(language),
            reported=values.get("country", "") == _reported(request) != "",
            check=self._turnstile.site_key if self._turnstile else "",
            confirm=await self._policies.site_confirmation(),
            mail=self._mail,
            password_games=password_games(self._games, language),
            referrer=await self._links.referrer(request),
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

    def _service(self, request: Request, identity: Identity | None) -> ServiceView | None:
        return self._services.view(identity.service, request.state.language) if identity else None

    def _unavailable(self, request: Request, identity: Identity | None) -> HTMLResponse:
        view = self._service(request, identity)
        rows = ("web.account.register.unavailable.service",) if identity else ()
        return message_page(
            self._pages,
            request,
            200,
            "web.account.register.unavailable",
            ("/sign-in", "web.account.sign_in"),
            rows,
            service=view.name if view else "",
            name=identity.name if identity else "",
        )


def _new_identity(request: Request) -> tuple[Identity, str] | None:
    # The answer of a service whose identity no account uses and that no proof is to link.
    pending = request.session.get(PENDING)
    if not pending or pending.get("account") is not None:
        return None
    identity = Identity(pending["service"], pending["subject"], pending["name"], pending["address"])
    return identity, pending["next"]


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
