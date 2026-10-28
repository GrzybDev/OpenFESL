import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from urllib.parse import urlencode

# A start link opens the launcher moments after the page that made it, so it works this long.
LIFETIME = timedelta(minutes=2)


class LinkState(StrEnum):
    """Where a start link stands: waiting for the launcher, taken by it, or ended unused."""

    WAITING = "waiting"
    TAKEN = "taken"
    ENDED = "ended"


@dataclass(slots=True)
class StartLink:
    """A one-time link with which OpenFESLHook's launcher starts a game for an account.

    Attributes:
        code: What the launcher trades for a session, once.
        ticket: What the page that made the link asks after it by; it signs nothing in.
        game_id: The game it starts.
        account_id: The account it signs in.
        epoch: The account's session epoch when it was made; signing out everywhere ends it.
        hero: The hero it starts the game as: the default one when it was made, and the one
            the launcher plays as once it signed in.
        made: When it was made.
        ends: When it stops working.
        used: Whether the launcher traded its code, whatever came of it.
        taken: When the launcher signed in with it, once it did.
    """

    code: str
    ticket: str
    game_id: str
    account_id: int
    epoch: int
    hero: str
    made: datetime
    ends: datetime
    used: bool = False
    taken: datetime | None = None

    @property
    def address(self) -> str:
        """The link the browser hands to the launcher of the game's copy that opens them."""
        query = urlencode({"role": "play", "code": self.code})
        return f"openfesl-{self.game_id}://launch?{query}"

    def state(self, now: datetime) -> LinkState:
        """Return where the link stands at a moment.

        Args:
            now: The moment.
        """
        if self.taken is not None:
            return LinkState.TAKEN
        if self.used or now >= self.ends:
            return LinkState.ENDED
        return LinkState.WAITING


class StartLinks:
    """The start links the site made, kept for the few minutes they work.

    They are kept in memory, as the launcher sessions they turn into are: a link outlives no
    restart of the server, which the page then shows as ended.
    """

    def __init__(
        self,
        lifetime: timedelta = LIFETIME,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Keep start links for as long as they work.

        Args:
            lifetime: How long a link works after it is made.
            clock: Reads the current time.
        """
        self._lifetime = lifetime
        self._clock = clock
        self._links: dict[str, StartLink] = {}
        self._codes: dict[str, StartLink] = {}

    def make(self, game_id: str, account_id: int, epoch: int, hero: str) -> StartLink:
        """Make a new start link for an account, ending the one it had for the game.

        Args:
            game_id: The game the link starts.
            account_id: The account it signs in.
            epoch: The account's session epoch, which the site's sign-in that asks for the link
                holds.
            hero: The hero the game is started as now.
        """
        now = self._clock()
        for link in list(self._links.values()):
            if now >= link.ends or (link.game_id, link.account_id) == (game_id, account_id):
                self._forget(link)
        link = StartLink(
            code=secrets.token_urlsafe(24),
            ticket=secrets.token_urlsafe(12),
            game_id=game_id,
            account_id=account_id,
            epoch=epoch,
            hero=hero,
            made=now,
            ends=now + self._lifetime,
        )
        self._links[link.ticket] = link
        self._codes[link.code] = link
        return link

    def find(self, ticket: str, account_id: int) -> StartLink | None:
        """Return one of an account's links by its ticket, while it is kept.

        Args:
            ticket: The link's ticket.
            account_id: The account that asks.
        """
        link = self._links.get(ticket)
        return link if link and link.account_id == account_id else None

    def take(self, code: str, game_id: str) -> StartLink | None:
        """Use a link's code up, returning the link unless it no longer works for the game.

        Args:
            code: The code, which works this once whatever comes of it.
            game_id: The game the launcher starts.
        """
        link = self._codes.pop(code, None)
        if link is None:
            return None
        link.used = True
        if link.game_id != game_id or self._clock() >= link.ends:
            return None
        return link

    def signed_in(self, link: StartLink, hero: str) -> None:
        """Note that the launcher signed in with a link it took.

        Args:
            link: The link.
            hero: The hero it plays as.
        """
        link.taken = self._clock()
        link.hero = hero

    def _forget(self, link: StartLink) -> None:
        del self._links[link.ticket]
        self._codes.pop(link.code, None)
