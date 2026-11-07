import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Sign-in with an address pauses after this many failed attempts within the window, and resumes
# once the oldest of them is older than the window.
FAILURES = 5
WINDOW = 600.0
# The mail that visitors can have sent goes to each address at most once in this long.
MAIL_INTERVAL = timedelta(minutes=10)


class Attempts:
    """Counts the failed sign-ins with each e-mail address, whatever account uses it, in memory.

    Counting addresses rather than accounts keeps a pause from telling whether an account uses the
    address. The counts live in this process only, as the server runs as one replica.
    """

    def __init__(
        self,
        limit: int = FAILURES,
        window: float = WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Pause sign-ins with an address after too many failures.

        Args:
            limit: How many failures within the window pause the address.
            window: How long a failure counts, in seconds.
            clock: Reads the current time in seconds.
        """
        self._limit = limit
        self._window = window
        self._clock = clock
        self._failures: dict[str, deque[float]] = {}
        self._purged = clock()

    def paused(self, address: str) -> bool:
        """Return whether sign-in with an address is paused.

        Args:
            address: The e-mail address, in any case.
        """
        return len(self._recent(_key(address))) >= self._limit

    def failed(self, address: str) -> None:
        """Count a failed sign-in with an address.

        Args:
            address: The e-mail address, in any case.
        """
        now = self._clock()
        if now - self._purged > self._window:
            # Addresses tried once and never again would otherwise stay for good.
            self._purged = now
            stale = [
                key
                for key, times in self._failures.items()
                if not times or now - times[-1] >= self._window
            ]
            for key in stale:
                del self._failures[key]
        self._failures.setdefault(_key(address), deque()).append(now)

    def clear(self, address: str) -> None:
        """Forget an address's failures, as once the right game password is set or used.

        Args:
            address: The e-mail address, in any case.
        """
        self._failures.pop(_key(address), None)

    def _recent(self, key: str) -> deque[float]:
        now = self._clock()
        times = self._failures.get(key, deque())
        while times and now - times[0] >= self._window:
            times.popleft()
        return times


def _key(address: str) -> str:
    return address.strip().casefold()


@dataclass(frozen=True, slots=True)
class Wait:
    """The wait before another mail may go to an address.

    Attributes:
        last: When the last mail went to it.
        until: From when the next one may go.
    """

    last: datetime
    until: datetime


class Mailings:
    """Lets one mail go to each e-mail address per interval, in memory.

    Every mail that a visitor can have sent to an address counts, so such forms cannot flood a
    mailbox, whoever asks.
    """

    def __init__(
        self,
        interval: timedelta = MAIL_INTERVAL,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Limit the mail to each address.

        Args:
            interval: How long after a mail the next one to the address may go.
            clock: Reads the current time.
        """
        self._interval = interval
        self._clock = clock
        self._sent: dict[str, datetime] = {}

    def reserve(self, address: str) -> Wait | None:
        """Count a mail to an address now, unless one went there lately.

        The check and the count are one step, so requests that run together send one mail.

        Args:
            address: The e-mail address, in any case.

        Returns:
            The wait before another mail may go to the address; None once this one is counted.
        """
        last = self._sent.get(_key(address))
        if last is not None and self._clock() - last < self._interval:
            return Wait(last, last + self._interval)
        self.sent(address)
        return None

    def sent(self, address: str) -> None:
        """Count a mail that went to an address now.

        Args:
            address: The e-mail address, in any case.
        """
        now = self._clock()
        for key in [key for key, last in self._sent.items() if now - last >= self._interval]:
            del self._sent[key]
        self._sent[_key(address)] = now
