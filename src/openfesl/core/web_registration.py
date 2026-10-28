from dataclasses import dataclass
from datetime import UTC, date, datetime

from openfesl.core.accounts import Account, AccountStore
from openfesl.core.games import Games
from openfesl.core.identities import Identity, IdentityStore
from openfesl.core.limits import Mailings, Wait
from openfesl.core.logins import Logins
from openfesl.core.mail import Outbox
from openfesl.core.policies import GamePolicies
from openfesl.core.problems import (
    Problem,
    address_problem,
    display_name_problem,
    password_problem,
)
from openfesl.core.registration import (
    NEEDS_CONSENT,
    TOO_YOUNG,
    Terms,
    birth_date_error,
    is_country,
)
from openfesl.core.verification import Confirmation, EmailVerification, new_link


@dataclass(frozen=True, slots=True)
class Registration:
    """What one of the site's registration forms sent.

    Attributes:
        address: The e-mail address, which the account logs in to games with.
        password: The game password; empty when the form leaves it out.
        display_name: The public display name.
        country: The ISO code of the chosen country.
        birth: The birth date's day, month and year as typed; checked and never kept.
        terms: Whether the terms were accepted.
    """

    address: str
    password: str
    display_name: str
    country: str
    birth: tuple[str, str, str]
    terms: bool


@dataclass(frozen=True, slots=True)
class TooYoung:
    """A birth date under the age from which accounts may be registered in its country.

    Attributes:
        age: That age.
        consent: Whether it is the age from which accounts register without a parent's
            consent, which a game's policy asks for.
    """

    age: int
    consent: bool


@dataclass(frozen=True, slots=True)
class Mailed:
    """A registration that went on by mail, which never tells whether the address has an account.

    A new address got the link that confirms it; an address with an account got a mail that says
    so, and nothing was made or linked.

    Attributes:
        address: The address.
    """

    address: str


@dataclass(frozen=True, slots=True)
class Confirmed:
    """An account whose address a link confirmed, with what the page that opens says about it.

    Attributes:
        waited: Whether it waited for the link to sign in and log in to games.
        service: The id of the first service linked to it, through which it was registered if
            any.
        password: Whether it has a game password.
    """

    waited: bool
    service: str | None
    password: bool


@dataclass(frozen=True, slots=True)
class IdentityTaken:
    """A service's identity that another account was linked to meanwhile, so nothing was made."""


# What came of a registration: the problems of its form, a birth date under the age limit, the wait
# while the address had mail lately, the identity linked elsewhere meanwhile, the mail that went
# out, or the account that signs in at once.
type Registered = list[Problem] | TooYoung | Wait | IdentityTaken | Mailed | Account


class WebRegistration:
    """Registers accounts on the site, directly or after a service's new identity."""

    def __init__(
        self,
        accounts: AccountStore,
        identities: IdentityStore,
        logins: Logins,
        policies: GamePolicies,
        games: Games,
        verification: EmailVerification,
        outbox: Outbox,
        mailings: Mailings,
        terms: Terms,
    ) -> None:
        """Register accounts under the site's policy.

        Args:
            accounts: Where the accounts are kept.
            identities: Where the identities linked to them are kept.
            logins: Hashes game passwords.
            policies: The site's policy, which says whether accounts wait for their link, and
                the games' age checks.
            games: The games, whose age limits every account meets.
            verification: Sends the links that confirm addresses.
            outbox: Sends the other mail.
            mailings: Limits the mail to each address.
            terms: The terms that registration accepts.
        """
        self._accounts = accounts
        self._identities = identities
        self._logins = logins
        self._policies = policies
        self._games = games
        self._verification = verification
        self._outbox = outbox
        self._mailings = mailings
        self._terms = terms

    async def offered(self) -> bool:
        """Return whether the site registers accounts.

        While the site's policy has accounts wait for their link, a server without mail could
        never send it.
        """
        return self._outbox.sends or not await self._policies.site_confirmation()

    async def suggested_name(self, name: str) -> str:
        """Return a name as the display name to suggest, when it fits the rules and is free.

        Args:
            name: The name, such as a service's name of the account.

        Returns:
            The name, or an empty string when it does not fit or another account uses it.
        """
        if display_name_problem(name) or await self._accounts.display_name_taken(name):
            return ""
        return name

    async def register(
        self, form: Registration, identity: Identity | None, language: str
    ) -> Registered:
        """Register an account from a form, linking a service's new identity to it if any.

        While the site's policy is on, every address gets the same answer: a new one the link
        that confirms it, one that has an account a mail that says so. While it is off, the
        account signs in at once, so an address in use is named, as games do.

        Args:
            form: What the form sent.
            identity: The identity that a service answered with, which becomes a way in; None
                for the direct registration.
            language: The language code of the page, which the mail is written in.
        """
        born = _birth_date(*form.birth)
        problems = [
            problem
            for problem in (
                address_problem(form.address),
                password_problem(form.password) if form.password or not identity else None,
                await self._display_name_problem(form.display_name),
                None if is_country(form.country) else Problem("country", "missing"),
                born if isinstance(born, Problem) else None,
                None if form.terms else Problem("terms", "missing"),
            )
            if problem
        ]
        if problems:
            return problems
        assert isinstance(born, date)
        if too_young := await self._too_young(born, form.country):
            return too_young
        confirm = await self._policies.site_confirmation()
        # Counted whatever the address belongs to, so a pause reveals no account.
        if confirm and (wait := self._mailings.reserve(form.address)):
            return wait
        found = await self._accounts.find(form.address)
        if found is None:
            account = await self._create(form, identity, language, confirm)
            if account is not None:
                return Mailed(account.nuid) if confirm else account
            # Another registration took the address, the display name or the identity meanwhile.
            if identity and await self._identities.account_of(identity.service, identity.subject):
                return IdentityTaken()
            found = await self._accounts.find(form.address)
            if found is None:
                return [Problem("display_name", "taken", form.display_name)]
        if not confirm:
            return [Problem("email", "taken")]
        if not found.account.server:
            links = {"link": "/sign-in", "reset": "/password-reset"}
            sent = {"sent": datetime.now(UTC)}
            self._outbox.send(form.address, "account.mail.in_use", language, links, sent)
        return Mailed(form.address)

    async def find(self, token: str) -> Account | Confirmation:
        """Return the account whose address a link confirms, without confirming it.

        Args:
            token: The token the link carries.

        Returns:
            The account, or why the link confirms nothing.
        """
        found = await self._verification.find(token)
        account = await self._accounts.account(found) if isinstance(found, int) else None
        if account is None:
            return found if isinstance(found, Confirmation) else Confirmation.UNKNOWN
        return account

    async def confirm(self, token: str) -> Confirmed | Confirmation:
        """Confirm the address of the account that a link went to, from a game or the site.

        Args:
            token: The token the link carries.

        Returns:
            The account that it confirmed, or why it confirmed nothing.
        """
        confirmed = await self._verification.confirm(token)
        found = await self._accounts.account(confirmed) if isinstance(confirmed, int) else None
        credentials = await self._accounts.find(found.nuid) if found else None
        if credentials is None:
            return confirmed if isinstance(confirmed, Confirmation) else Confirmation.UNKNOWN
        account = credentials.account
        linked = await self._identities.linked(account.id)
        return Confirmed(
            await self._policies.confirms(self._games.by_id(account.made_in or "")),
            linked[0].service if linked else None,
            credentials.password_hash is not None,
        )

    async def _display_name_problem(self, name: str) -> Problem | None:
        if problem := display_name_problem(name):
            return problem
        if await self._accounts.display_name_taken(name):
            return Problem("display_name", "taken", name)
        return None

    async def _too_young(self, born: date, country: str) -> TooYoung | None:
        # An account plays every game, so the highest of their limits counts.
        refusals = []
        for game in self._games:
            ages = game.ages(country)
            policies = (await self._policies.of(game)).registration
            error = birth_date_error(born.year, born.month, born.day, ages, policies)
            if error and error.field_error == TOO_YOUNG:
                refusals.append(TooYoung(ages.registration_age, consent=False))
            elif error and error.field_error == NEEDS_CONSENT:
                refusals.append(TooYoung(ages.parental_age, consent=True))
        return max(refusals, key=lambda refusal: refusal.age, default=None)

    async def _create(
        self, form: Registration, identity: Identity | None, language: str, confirm: bool
    ) -> Account | None:
        password_hash = await self._logins.hash(form.password) if form.password else None
        # Every web account that the server can mail gets its link, so its address shows as not
        # confirmed until its link confirms it, even while the policy lets it in without.
        token, link = new_link() if self._outbox.sends else (None, None)
        account = await self._accounts.add(
            form.address,
            password_hash,
            form.country.upper(),
            self._terms.version,
            verification=link,
            display_name=form.display_name,
            identity=identity,
        )
        if account is None:
            return None
        if token and link:
            texts = "verification.mail" if confirm else "account.mail.address"
            self._verification.send(account.nuid, token, link, language, texts)
            self._mailings.sent(account.nuid)
        return account


def _birth_date(day: str, month: str, year: str) -> date | Problem:
    if not (day.isdigit() and month.isdigit() and year.isdigit() and len(year) == 4):
        return Problem("birth", "form")
    try:
        born = date(int(year), int(month), int(day))
    except ValueError:
        if not 1 <= int(month) <= 12:
            return Problem("birth", "form")
        return Problem("birth", "day", f"{int(day)}-{int(month)}")
    if born > datetime.now(UTC).date():
        return Problem("birth", "future")
    return born
