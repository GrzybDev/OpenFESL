import hashlib
import hmac
import logging
import re
import secrets
import struct
import uuid
from collections import deque
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum, StrEnum, auto
from typing import Protocol

from openfesl.core.admins import Admin
from openfesl.core.theater import UNAUTHORIZED, TheaterError

logger = logging.getLogger(__name__)

BAD_SECRET = "bsec"

# A dedicated server refuses to start unless the five little-endian words of its
# ProviderId.dat add up to this.
PROVIDER_CHECK = 0xC18E65EF
PROVIDER_ID_SIZE = 16
# The game keeps this many bytes of an attribute value, which a provider's name becomes.
MAXIMUM_PROVIDER_NAME = 63

# The ids the master server issues, the only ones it looks up.
_UGID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# A provider id XORed with 8 mask bytes, then the mask, in hex.
_MASKED_PROVIDER = re.compile(r"[0-9A-Fa-f]{48}")


class HostingMode(StrEnum):
    """Whose games players see: every dedicated server's, or only those the admin approved."""

    OPEN = "open"
    APPROVED = "approved"


class ServerState(StrEnum):
    """What the admin decided about a dedicated server."""

    PENDING = "pending"
    APPROVED = "approved"
    REVOKED = "revoked"


class Standing(Enum):
    """Where the games of a dedicated server stand with players as of now."""

    LISTED = auto()
    """Players see them and may join."""
    HIDDEN = auto()
    """They wait for the admin's approval."""
    REFUSED = auto()
    """The admin revoked the server or its provider."""


@dataclass(frozen=True, slots=True)
class Identity:
    """A host's persistent game id and the secret that proves it is its own."""

    ugid: str
    secret: str


@dataclass(frozen=True, slots=True)
class Provider:
    """An operator of dedicated servers, known by the id their ProviderId.dat holds.

    Attributes:
        id: The 16 bytes before the file's check word.
        name: What the admin calls it, which game lists show for its servers' games.
        revoked: Whether the admin revoked it, and with it every server that reports it.
    """

    id: bytes
    name: str
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class GameServer:
    """A dedicated server instance, known by the identity the master server issued it.

    Attributes:
        ugid: Its persistent game id, which it keeps in its serverid.dat.
        secret_hash: The SHA-256 digest of the secret it proves the id with.
        name: The name it listed its first game with, which tells the admin which server it is.
        state: What the admin decided about it.
        provider_id: The provider id its games last reported, known to the admin or not.
        provider: The provider the admin created with that id, if any.
        first_seen: When it first tried to host; None for one not stored yet.
    """

    ugid: str
    secret_hash: bytes
    name: str
    state: ServerState = ServerState.PENDING
    provider_id: bytes | None = None
    provider: Provider | None = None
    first_seen: datetime | None = None

    @property
    def revoked(self) -> bool:
        """Whether the admin revoked the server or its provider."""
        return self.state is ServerState.REVOKED or bool(self.provider and self.provider.revoked)

    def standing(self, mode: HostingMode) -> Standing:
        """Return where the server's games stand with players.

        Args:
            mode: The hosting mode.
        """
        if self.revoked:
            return Standing.REFUSED
        if self.state is ServerState.APPROVED or mode is HostingMode.OPEN:
            return Standing.LISTED
        return Standing.HIDDEN


@dataclass(frozen=True, slots=True)
class Attempts:
    """How often a dedicated server whose games players do not see tried to host lately.

    Attributes:
        last_hour: Its attempts in the last hour.
        today: Its attempts since midnight UTC.
        refused_today: Those of today's attempts that were refused, as the admin revoked it.
        last: When it last tried; None when it has not since the server started.
    """

    last_hour: int = 0
    today: int = 0
    refused_today: int = 0
    last: datetime | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """The admin's latest decision about a dedicated server.

    Attributes:
        state: What was decided.
        at: When.
        admin: The display name of the admin who decided; None for the command line.
    """

    state: ServerState
    at: datetime
    admin: str | None


@dataclass(frozen=True, slots=True)
class ProviderHistory:
    """When a provider was added and revoked, and by whom.

    Attributes:
        added: When it was added.
        added_by: The display name of the admin who added it; None for the command line.
        revoked: When it was revoked; None while it is not, or when no record says.
        revoked_by: The display name of the admin who revoked it; None for the command line.
    """

    added: datetime
    added_by: str | None = None
    revoked: datetime | None = None
    revoked_by: str | None = None


@dataclass(frozen=True, slots=True)
class Admission:
    """How a host lists a game: under which identity, as which server, and whether it shows.

    Attributes:
        identity: The game id and secret the CGAM answer hands the host.
        server: The dedicated server it is; none for a player's own playgroup.
        listed: Whether players see the game now.
    """

    identity: Identity
    server: GameServer | None
    listed: bool


class ServerStore(Protocol):
    """Where the hosting mode, the providers and the dedicated servers are kept."""

    async def hosting_mode(self) -> HostingMode:
        """Return whose games players see; only approved servers' until the admin opens hosting."""

    async def set_hosting_mode(self, mode: HostingMode) -> None:
        """Change whose games players see.

        Args:
            mode: The new mode.
        """

    async def add_provider(self, provider: Provider, admin: Admin | None) -> bool:
        """Keep a new provider, unless another has its name, recording who added it.

        Args:
            provider: The provider, with an id no other provider has.
            admin: The admin who added it; None for the command line.

        Returns:
            Whether it was kept: names are unique, compared without case.
        """

    async def providers(self) -> list[Provider]:
        """Return every provider, oldest first."""

    async def revoke_provider(self, provider_id: bytes, admin: Admin | None) -> bool:
        """Revoke a provider, which revokes every server that reports it, recording who did.

        Args:
            provider_id: The provider's id.
            admin: The admin who revoked it; None for the command line.

        Returns:
            Whether a provider has that id.
        """

    async def provider_history(self) -> dict[bytes, ProviderHistory]:
        """Return when each provider was added and revoked, and by whom, by its id."""

    async def add(self, server: GameServer) -> None:
        """Keep a new server.

        Args:
            server: The server, with an id no other server has.
        """

    async def find(self, ugids: Collection[str]) -> list[GameServer]:
        """Return the servers with some persistent game ids, each with its known provider.

        Args:
            ugids: The ids.
        """

    async def attach(self, ugid: str, provider_id: bytes) -> GameServer | None:
        """Record the provider id that a server's games report.

        Args:
            ugid: The server's persistent game id.
            provider_id: The provider id.

        Returns:
            The server as it is now, or None when no server has that id.
        """

    async def set_state(self, ugid: str, state: ServerState, admin: Admin | None) -> bool:
        """Approve or revoke a server, recording the decision among the admin's actions.

        Args:
            ugid: The server's persistent game id.
            state: The admin's decision.
            admin: The admin who decided; None for the command line.

        Returns:
            Whether a server has that id.
        """

    async def all(self) -> list[GameServer]:
        """Return every server that ever hosted, oldest first."""

    async def decisions(self, ugids: Collection[str]) -> dict[str, Decision]:
        """Return the admin's latest decision about each of some servers that has one.

        Args:
            ugids: The servers' persistent game ids.
        """


def new_identity() -> Identity:
    """Return a fresh persistent game id and secret.

    The game keeps 36 characters of an id and 127 of a secret, and printable ASCII without
    spaces, commas, `%`, `"` or `=` passes its codec and its serverid.dat unchanged.
    """
    return Identity(str(uuid.uuid4()), secrets.token_urlsafe(48))


def new_provider(name: str) -> Provider:
    """Return a provider with a fresh random id.

    Args:
        name: What the admin calls it.

    Raises:
        ValueError: When game lists could not show the name whole.
    """
    if not 0 < len(name.encode("utf-8")) <= MAXIMUM_PROVIDER_NAME:
        raise ValueError(f"A provider's name takes 1 to {MAXIMUM_PROVIDER_NAME} bytes")
    return Provider(secrets.token_bytes(PROVIDER_ID_SIZE), name)


def provider_file(provider_id: bytes) -> bytes:
    """Return the ProviderId.dat a provider's dedicated servers read: its id and a check word.

    Args:
        provider_id: The provider's 16-byte id.
    """
    words = struct.unpack("<4I", provider_id)
    return provider_id + struct.pack("<I", (PROVIDER_CHECK - sum(words)) % 2**32)


def reported_provider(value: str) -> bytes | None:
    """Return the provider id in the provider attribute of a dedicated server's game, if any.

    The server XORs the id from its ProviderId.dat with 8 random bytes on every start and
    sends both in hex, so only the decoded id stays the same.

    Args:
        value: The attribute's value.
    """
    if not _MASKED_PROVIDER.fullmatch(value):
        return None
    data = bytes.fromhex(value)
    masked, mask = data[:PROVIDER_ID_SIZE], data[PROVIDER_ID_SIZE:]
    return bytes(byte ^ mask[index % len(mask)] for index, byte in enumerate(masked))


class Approval:
    """Issues dedicated servers their identities and decides whether players see their games."""

    def __init__(self, store: ServerStore) -> None:
        """Decide by the servers, the providers and the mode in a store.

        Args:
            store: Where the hosting mode, the providers and the servers are kept.
        """
        self._store = store
        self._tried: dict[str, deque[tuple[datetime, bool]]] = {}

    def attempts(self, ugid: str) -> Attempts:
        """Return how often a server whose games players do not see tried to host lately.

        Args:
            ugid: The server's persistent game id.
        """
        tried = self._tried.get(ugid, ())
        now = datetime.now(UTC)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return Attempts(
            last_hour=sum(at > now - timedelta(hours=1) for at, _ in tried),
            today=sum(at >= midnight for at, _ in tried),
            refused_today=sum(at >= midnight and refused for at, refused in tried),
            last=tried[-1][0] if tried else None,
        )

    async def admit(self, ugid: str, secret: str, name: str) -> Admission:
        """Let a dedicated server host under the identity it sent, or under a new one.

        A server sends the id and secret of an earlier CGAM answer again; one without an id
        the master issued gets a new identity, which waits for the admin's approval.

        Args:
            ugid: The persistent game id the server sent, empty when it keeps none.
            secret: The secret it sent with the id.
            name: The name of the game it lists.

        Raises:
            TheaterError: When the secret is wrong, which the server reports as another server
                running under its id, or the admin revoked the server or its provider.
        """
        found = await self._store.find([ugid]) if _UGID.fullmatch(ugid) and secret else []
        if found:
            server = found[0]
            if not hmac.compare_digest(server.secret_hash, _digest(secret)):
                logger.warning("Refusing server %s: its secret is wrong", ugid)
                raise TheaterError(BAD_SECRET)
            identity = Identity(ugid, secret)
        else:
            identity = new_identity()
            # The name only tells the admin which server it is; what text cannot hold goes.
            label = name.encode("utf-8", "replace").decode("utf-8").replace("\0", "")
            server = GameServer(identity.ugid, _digest(identity.secret), label)
            await self._store.add(server)
            logger.info("Issued the server id %s to %r", server.ugid, label)
        standing = server.standing(await self._store.hosting_mode())
        if standing is not Standing.LISTED:
            self._note_attempt(server.ugid, refused=standing is Standing.REFUSED)
        if standing is Standing.REFUSED:
            logger.warning("Refusing server %s: the admin revoked it or its provider", ugid)
            raise TheaterError(UNAUTHORIZED)
        return Admission(identity, server, listed=standing is Standing.LISTED)

    async def attach(self, ugid: str, provider_id: bytes) -> GameServer | None:
        """Record the provider that a dedicated server's games report.

        Args:
            ugid: The server's persistent game id.
            provider_id: The provider id its provider attribute carries.

        Returns:
            The server as it is now, or None when it is no longer known.
        """
        server = await self._store.attach(ugid, provider_id)
        if server is not None and server.provider is None:
            logger.info(
                "Server %s reports the unknown provider %s", ugid, provider_id.hex().upper()
            )
        return server

    async def mode(self) -> HostingMode:
        """Return whose games players see now: every dedicated server's or approved ones'."""
        return await self._store.hosting_mode()

    async def standings(self, ugids: Collection[str]) -> dict[str, Standing]:
        """Return where the games of dedicated servers stand with players as of now.

        Args:
            ugids: The servers' persistent game ids; ids no server has are left out.
        """
        mode = await self._store.hosting_mode()
        return {server.ugid: server.standing(mode) for server in await self._store.find(ugids)}

    def _note_attempt(self, ugid: str, refused: bool) -> None:
        # Kept for a day, in memory: the counts only tell the admin what a server does lately.
        tried = self._tried.setdefault(ugid, deque())
        now = datetime.now(UTC)
        tried.append((now, refused))
        while tried[0][0] < now - timedelta(days=1):
            tried.popleft()


def _digest(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8", "surrogateescape")).digest()
