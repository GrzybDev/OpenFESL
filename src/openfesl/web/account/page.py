from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.account_management import AccountManagement, AccountOverview
from openfesl.core.accounts import Account
from openfesl.core.games import Game, Games
from openfesl.core.limits import Wait
from openfesl.core.personas import Persona
from openfesl.core.policies import GamePolicies
from openfesl.core.problems import Problem
from openfesl.core.rankings import RankingStore
from openfesl.core.registration import Terms, countries
from openfesl.core.sign_in import LinkSent, WebSignIn
from openfesl.core.stats import bucket_of, partition_of
from openfesl.core.texts import translate
from openfesl.core.verification import VALID_FOR
from openfesl.protocol.rank import Period
from openfesl.web.account.fields import field_errors
from openfesl.web.account.services import SignInServices
from openfesl.web.account.session import ACCOUNT, FORM, account_of, answered, open_page, shown
from openfesl.web.account.sign_in import password_games
from openfesl.web.pages import Pages, Toast
from openfesl.web.sessions import form_matches, form_token, toast
from openfesl.web.turnstile import Turnstile


@dataclass(frozen=True, slots=True)
class PersonaLine:
    """A persona as the account page lists it under its game.

    Attributes:
        persona: The persona.
        rank: Its rank, in a game whose leaderboard ranks its players.
        score: Its total score there.
    """

    persona: Persona
    rank: int | None = None
    score: int = 0


class AccountPages:
    """The account page, where a signed-in player sees their account and changes it."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        accounts: Callable[[int], Awaitable[Account | None]],
        management: Callable[[], AccountManagement],
        sign_in: Callable[[], WebSignIn],
        services: SignInServices,
        policies: GamePolicies,
        rankings: RankingStore,
        terms: Terms,
        mail: bool,
        turnstile: Turnstile | None,
    ) -> None:
        """Prepare the account page.

        Args:
            pages: Renders the page.
            games: The games, each of which the page lists with the account's personas.
            accounts: Returns an account by its number.
            management: Returns the account management, once the application has started.
            sign_in: Returns the site's sign-in, which sends new confirmation links.
            services: The sign-in services, which the page offers to link.
            policies: The site's and each game's policies, which say whether an account waits
                for its confirmation link.
            rankings: The players' stats, whose ranks and scores the page shows.
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
        self._services = services
        self._policies = policies
        self._rankings = rankings
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
            games=[
                (entry.game, await self._lines(entry.game, entry.personas))
                for entry in overview.games
            ],
            sent=waiting - VALID_FOR if waiting else None,
            waits=bool(waiting) and await self._policies.confirms(made_in),
            mail=self._mail,
            check=self._turnstile.site_key if self._turnstile and waiting and self._mail else "",
            accepted=account.terms_version == self._terms.version,
            password_games=password_games(self._games, language),
            game_names=[translate(f"games.{game.id}.short", language) for game in self._games],
            services=self._services.views(language),
            personas=[translate(f"games.{game.id}.personas", language) for game in self._games],
            failed=Toast("web.toast.failed", {}, error=True),
        )
        if self._turnstile:
            self._turnstile.allow(page)
        self._services.allow_forms(page)
        return page

    async def _lines(self, game: Game, personas: list[Persona]) -> list[PersonaLine]:
        # The ranks and scores of the personas in a game whose leaderboard ranks its players.
        if "leaderboard" not in game.pages or not personas:
            return [PersonaLine(persona) for persona in personas]
        found = await self._rankings.lines(
            partition_of(game),
            {"score": game.stats.score},
            "score",
            bucket_of(Period.ALL_TIME, 0),
            [persona.id for persona in personas],
            slice(0, len(personas)),
        )
        scores = {line.persona_id: int(line.values["score"]) for line in found}
        lines = []
        for persona in personas:
            score = scores.get(persona.id, 0)
            lines.append(PersonaLine(persona, game.stats.rank(score), score))
        return lines
