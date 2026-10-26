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
    PLAYER_KICK = "player_kick"
    """Kicked the persona numbered as the target from the games it joins, plays or waits for."""
    SESSION_END = "session_end"
    """Ended the session that the account numbered as the target is logged in on."""
    LOGINS_FORGET = "logins_forget"
    """Removed logins that the game remembers for the account numbered as the target; the
    details hold how many."""
    PROVIDER_ADD = "provider_add"
    """Added the provider whose id, in hex, is the target; the details hold its name."""
    PROVIDER_REVOKE = "provider_revoke"
    """Revoked the provider whose id, in hex, is the target, and with it its servers."""
    SERVER_STATE = "server_state"
    """Approved or revoked the dedicated server whose persistent game id is the target; the
    details hold its new state."""
    ENTITLEMENT_GRANT = "entitlement_grant"
    """Granted the account numbered as the target entitlements by hand; the details hold their
    tags and the product key whose grants they were, if any."""
    ENTITLEMENT_STATE = "entitlement_state"
    """Revoked an entitlement of the account numbered as the target, or granted it again; the
    details hold its tag and new status."""
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
    """Applies the admin's actions to the running server as the log records them.

    The command line records them in another process, so the log is read every few seconds;
    the admin panel has its own actions applied at once.
    """

    def __init__(self, log: AdminLog) -> None:
        """Follow a log of the admin's actions.

        Args:
            log: Where the actions are recorded.
        """
        self._log = log
        self._handlers: dict[AdminAction, list[Callable[[LoggedAction], object]]] = {}
        self._after = 0
        # The panel's requests and the periodic check may read the log at once; each action
        # is applied once.
        self._reading = asyncio.Lock()

    def on(self, action: AdminAction, handler: Callable[[LoggedAction], object]) -> None:
        """Call a function for each new action of a kind.

        Args:
            action: The kind.
            handler: Called with the action.
        """
        self._handlers.setdefault(action, []).append(handler)

    async def start(self) -> None:
        """Take the actions recorded so far as applied, as when the server starts."""
        self._after = await self._log.last()

    async def apply(self) -> None:
        """Hand each action recorded since the last check to its handlers."""
        async with self._reading:
            for action in await self._log.since(self._after):
                self._after = action.id
                for handler in self._handlers.get(action.action, []):
                    try:
                        handler(action)
                    except Exception:
                        logger.exception("Could not apply the admin's action %d", action.id)

    async def follow(self) -> None:
        """Apply the actions as the log records them until cancelled."""
        while True:
            await asyncio.sleep(ADMIN_CHECK_INTERVAL)
            try:
                await self.apply()
            except Exception:
                # The database may be away for a moment; the next check tries again.
                logger.exception("Could not read the admin's actions")
