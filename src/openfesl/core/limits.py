import time
from collections import deque
from collections.abc import Callable

# Sign-in with an address pauses after this many failed attempts within the window, and resumes
# once the oldest of them is older than the window.
FAILURES = 5
WINDOW = 600.0


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
                key for key, times in self._failures.items() if now - times[-1] >= self._window
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
