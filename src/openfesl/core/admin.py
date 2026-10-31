import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from inspect import isawaitable
from typing import Any, Protocol

from openfesl.core.admins import Role

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
    DELETION_REQUEST = "deletion_request"
    """Not the admin's: the player of the account numbered as the target asked on the site for
    its deletion, which deactivated it; the details hold its new state as for account_state."""
    PERSONA_STATE = "persona_state"
    """Changed the state of the persona numbered as the target; the details hold the state, and
    for a hero the admin's note and the hero that became the default instead, if one did."""
    HERO_RENAME = "hero_rename"
    """Gave the hero whose persona is numbered as the target a new name; the details hold the
    names before and after and the admin's note."""
    HERO_DEFAULT = "hero_default"
    """Made the hero whose persona is numbered as the target its account's default hero; the
    details hold the name of the default hero before, if any, and the admin's note."""
    HERO_SLOTS = "hero_slots"
    """Granted the account numbered as the target further hero slots of a game; the details
    hold the game, how many, the slots before and after and the admin's note."""
    CHESTS = "chests"
    """Granted the hero whose persona is numbered as the target treasure chests, or took some;
    the details hold how many, below 0 for a take, the chests before and after and the note."""
    CHEST_KEYS = "chest_keys"
    """Granted the account numbered as the target treasure chest keys of a game, or took some;
    the details hold the game, how many, below 0 for a take, the keys before and after and the
    note."""
    WALLET_CHANGE = "wallet_change"
    """Granted to a wallet of the account numbered as the target, or took from it; the details
    hold the game, the currency, the hero whose own wallet it is, if any, the amount, below 0
    for a take, the balance before, the ledger entry's number and the admin's note."""
    ITEM_GRANT = "item_grant"
    """Granted the hero whose persona is numbered as the target an item; the details hold its
    number, how the hero gained it, how it holds the item then and the admin's note."""
    ITEM_CHANGE = "item_change"
    """Changed when a rental of the hero whose persona is numbered as the target ends, or the uses
    it has left of an item; the details hold the item's number, both values and the note."""
    ITEM_REVOKE = "item_revoke"
    """Took an item out of use of the hero whose persona is numbered as the target; the details
    hold its number and the admin's note."""
    MUGSHOT_REMOVE = "mugshot_remove"
    """Removed the portrait that the game sent of the hero whose persona is numbered as the
    target; the details hold the admin's note."""
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
    SERVER_OFFICIAL = "server_official"
    """Marked the dedicated server whose persistent game id is the target as official, or removed
    the mark; the details say which."""
    ENTITLEMENT_GRANT = "entitlement_grant"
    """Granted the account numbered as the target entitlements by hand; the details hold their
    tags and the product key whose grants they were, if any."""
    ENTITLEMENT_STATE = "entitlement_state"
    """Revoked an entitlement of the account numbered as the target, or granted it again; the
    details hold its tag and new status."""
    KEY_ADD = "key_add"
    """Added the product key named as the target; the details say whether it is a starting key
    of the game data."""
    KEY_STATE = "key_state"
    """Enabled or disabled the product key named as the target; the details say which."""
    KEY_GRANTS = "key_grants"
    """Changed what the product key named as the target grants and its public labels; the
    details hold its tags and labels."""
    KEY_LIMITS = "key_limits"
    """Changed how often the product key named as the target may be redeemed; the details hold
    its most uses, none for any number, and whether an account may redeem it only once."""
    GAME_ICON = "game_icon"
    """Stored or removed the icon of the game whose id is the target; the details say whether
    the game has one now."""
    CATALOGUE_IMPORT = "catalogue_import"
    """Replaced the catalogue of the game named as the target with one from a player's copy; the
    details hold how many items it has, in how many languages and how many icons were kept."""
    CATALOGUE_ART = "catalogue_art"
    """Chose who sees the art of the game named as the target; the details hold the viewers and
    how many icons, hero models, textures and portraits the choice covers."""
    GAME_MARK = "game_mark"
    """Turned the own server mark of the game whose id is the target on or off at its official
    servers; the details say which and how many official servers it had then."""
    STORE_PRICES = "store_prices"
    """Changed the price points of an item of a game's store, the target naming both as
    game:item; the details hold each changed point's product number, currency, ownership, price
    before and after and whether it was and is sold, and whether the admin reset them to the
    rules."""
    BOX_TABLE = "box_table"
    """Changed the drop table of a box of a game's store, the target naming both as game:box;
    the details hold the tiers' weights before and after, the entries removed and added, and
    whether the box promised its worth before and after."""
    BOX_SWITCH = "box_switch"
    """Put a box of a game's store on sale or took it off, or let keys open its chests or not,
    the target naming both as game:box; the details hold the box's key and kind and whether it
    is on."""
    DEAL_ROTATION = "deal_rotation"
    """Turned the hot-deal rotation of the game whose id is the target on or off; the details
    say which."""
    DEAL_SECTION = "deal_section"
    """Changed a section of a game's hot-deal rotation, the target naming both as game:section;
    the details hold the section's values before and after."""
    DEAL_ART = "deal_art"
    """Uploaded a picture of a hot deal's offer of the game whose id is the target, or removed
    the offer's uploaded pictures; the details hold the offer, whether the picture is the big
    one, and whether the pictures were removed."""
    DEAL_OFFER = "deal_offer"
    """Added or changed an admin offer among a game's hot deals, the target naming both as
    game:bundle number; the details hold the offer's name, section, price, start, end and how
    many items it holds, and whether it is new."""
    STORE_ACCOUNT = "store_account"
    """Changed an account price, allowance or per-round cap of a game, the target naming both
    as game:value, such as bfheroes:slot; the details hold the value's name and the value before
    and after."""
    STORE_RULE = "store_rule"
    """Changed a price rule of a game's store and applied it, the target naming both as
    game:rule; the details hold the rule's values before and after, how many items took its
    prices and how many kept their own."""
    REWARD_PAUSE = "reward_pause"
    """Paused the claims of the rewards of the game whose id is the target, or let them go on;
    the details say which and hold the admin's note."""
    REWARD_SWITCH = "reward_switch"
    """Switched a reward of the game whose id is the target on or off: an event, a part of one,
    the starter packs or the first chest's key; the details hold its path and the new state."""
    REWARD_MILESTONE = "reward_milestone"
    """Changed a milestone of the rewards of the game whose id is the target; the details hold
    its key and its Funds and switch before and after."""
    REWARD_ZONE = "reward_zone"
    """Changed the time zone at whose midnight the days of the rewards of the game whose id is
    the target start; the details hold the zone before and after."""
    REWARD_EVENT = "reward_event"
    """Changed an event of the rewards of the game whose id is the target; the details hold its
    key, the first and last days of its run before and after, and its own text before and
    after."""


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


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One of the admin's actions as the audit log lists it.

    Attributes:
        id: Its number; later actions have higher ones.
        at: When it was done.
        admin: The admin's subject at the sign-in provider; None for the command line.
        name: The admin's display name then, if known.
        role: The admin's role then, if known.
        action: What the admin did.
        target: What it was done to.
        details: What changed, as the action defines.
    """

    id: int
    at: datetime
    admin: str | None
    name: str | None
    role: Role | None
    action: AdminAction
    target: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """Which of the admin's actions the audit log lists; what is None does not filter.

    Attributes:
        admin: The subject of the admin who did them; empty for the command line.
        account: The account they were done to, or to whose personas.
        personas: The account's personas.
    """

    admin: str | None = None
    account: int | None = None
    personas: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Auditor:
    """Someone who acted, as the audit log's filter offers them.

    Attributes:
        subject: The admin's subject; None for the command line.
        name: Their latest display name, if known.
        role: Their latest role, if known.
        count: How many of the actions are theirs.
    """

    subject: str | None
    name: str | None
    role: Role | None
    count: int


class ActionHistory(Protocol):
    """Where pages read who last did something to a target and when."""

    async def latest(self, action: AdminAction, target: str) -> AuditEntry | None:
        """Return the latest action of a kind done to a target, if any was.

        Args:
            action: The kind.
            target: What it was done to.
        """


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
            handler: Called with the action; what it returns is awaited when it can be.
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
                        if isawaitable(applied := handler(action)):
                            await applied
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
