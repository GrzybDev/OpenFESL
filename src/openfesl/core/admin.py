import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# The admin acts in another process, so the server reads the log of their actions this often.
ADMIN_CHECK_INTERVAL = 5.0


class AdminAction(StrEnum):
    """What an admin did, as the log of their actions records it with its target and details."""

    SETTING = "setting"
    """Changed the setting named as the target; the details hold its value, none when reset."""
    ACCOUNT_STATE = "account_state"
    """Changed the state of the account numbered as the target; the details hold the state and
    when it ends, if it does."""
    ACCOUNT_VERIFIED = "account_verified"
    """Confirmed the e-mail address of the account numbered as the target by hand."""
    PERSONA_STATE = "persona_state"
    """Changed the state of the persona numbered as the target; the details hold the state."""
    KEY_LIMITS = "key_limits"
    """Changed how often the product key named as the target may be redeemed; the details hold
    its most uses, none for any number, and whether an account may redeem it only once."""


@dataclass(frozen=True, slots=True)
class LoggedAction:
    """One of the admin's actions as the log keeps it.

    Attributes:
        id: Its number; later actions have higher ones.
        action: What the admin did.
        target: What it was done to.
        details: What changed, as the action defines.
    """

    id: int
    action: AdminAction
    target: str
    details: dict[str, Any]


class AdminLog(Protocol):
    """Where the admin's actions are recorded, each with its time."""

    async def last(self) -> int:
        """Return the number of the latest action; 0 before the first."""

    async def since(self, after: int) -> list[LoggedAction]:
        """Return the actions recorded after one, oldest first.

        Args:
            after: The number of the last action already seen.
        """


class AdminFeed:
    """Applies the admin's actions to the running server as the log records them."""

    def __init__(self, log: AdminLog) -> None:
        """Follow a log of the admin's actions.

        Args:
            log: Where the actions are recorded.
        """
        self._log = log
        self._handlers: dict[AdminAction, list[Callable[[LoggedAction], object]]] = {}

    def on(self, action: AdminAction, handler: Callable[[LoggedAction], object]) -> None:
        """Call a function for each new action of a kind.

        Args:
            action: The kind.
            handler: Called with the action.
        """
        self._handlers.setdefault(action, []).append(handler)

    async def follow(self, after: int) -> None:
        """Hand each new action to its handlers until cancelled.

        Args:
            after: The number of the last action already applied, as when the server started.
        """
        while True:
            await asyncio.sleep(ADMIN_CHECK_INTERVAL)
            try:
                actions = await self._log.since(after)
            except Exception:
                # The database may be away for a moment; the next check tries again.
                logger.exception("Could not read the admin's actions")
                continue
            for action in actions:
                after = action.id
                for handler in self._handlers.get(action.action, []):
                    try:
                        handler(action)
                    except Exception:
                        logger.exception("Could not apply the admin's action %d", action.id)
