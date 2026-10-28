import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from openfesl.core.admin import AdminAction, AdminFeed, LoggedAction
from openfesl.core.connection import PresenceOutbox
from openfesl.core.game_icons import GameIcons
from openfesl.core.game_pages import Names
from openfesl.core.games import Game
from openfesl.core.hosting import GAME, HostedGame, Hosting, Player, PlayerState
from openfesl.core.texts import language_of, translate
from openfesl.protocol.relay import Activity, Party, PresenceUpdate

if TYPE_CHECKING:
    from openfesl.core.session import GameSession

logger = logging.getLogger(__name__)

# The hook passes at most five changes in 20 seconds on to the chat client, so each session
# gets at most one frame per interval, with the latest state.
UPDATE_INTERVAL = 5.0

# The small image's tooltip: the service that the presence comes from.
SERVICE = "OpenFESL"

# An IPv4 address with its port and the brackets around them, as hosts put theirs in names.
_ADDRESS = re.compile(r"[(\[]?\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b[)\]]?")
# A name that a host labels with its data center and address, "(unknown)" for an address it
# does not know: [iad]Name(203.0.113.7:18567) or [iad]Name((unknown):18567).
_LABELLED = re.compile(r"\[[^\[\]]*\](?P<name>.*)\((?:\(unknown\)|[^()\s]+):\d{1,5}\)")


@dataclass(frozen=True, slots=True)
class PresenceArt:
    """The public addresses of the images that presence shows; without them it shows none.

    Attributes:
        game: The large image, the game's icon, in which {game} stands for the game's id; shown
            for the games that have one.
        service: The small image, the service's icon.
    """

    game: str | None = None
    service: str | None = None


@dataclass(slots=True)
class _Follower:
    outbox: PresenceOutbox
    sent: PresenceUpdate | None = None
    # The hosted game the player waits for or plays in, and since when.
    game_id: int | None = None
    since: datetime | None = None
    # Whether its presence failed last time, so that a lasting failure is logged once.
    failing: bool = False


class RichPresence:
    """What players' chat clients show about their games, sent to the hooks that ask for it.

    Each session's presence follows its live state: the menus, a game's queue, joining it, and
    the match with its map, mode and players. Sessions that host games get none, since a chat
    client would show a headless server as the player's game.
    """

    def __init__(self, hosting: Hosting, application: str | None, art: PresenceArt) -> None:
        """Build presence from the games that hosts list.

        Args:
            hosting: The games hosts list and their players.
            application: The id of the chat application that shows the presence; without one
                no presence is sent.
            art: The images that presence shows.
        """
        self._hosting = hosting
        self._application = application
        self._art = art
        self._followers: dict[GameSession, _Follower] = {}
        # The games whose icon the large image shows.
        self._icons: set[str] = set()

    async def follow_icons(self, icons: GameIcons, feed: AdminFeed) -> None:
        """Learn which games have icons, and follow the admin's changes to them.

        Args:
            icons: Where the games' icons are kept.
            feed: The admin's actions as they are recorded.
        """
        feed.on(AdminAction.GAME_ICON, self._icon_changed)
        self._icons = await icons.games()

    def _icon_changed(self, action: LoggedAction) -> None:
        if action.details.get("stored"):
            self._icons.add(action.target)
        else:
            self._icons.discard(action.target)

    def follow(self, session: GameSession, outbox: PresenceOutbox) -> None:
        """Send a session's presence from now on, whenever it changes.

        Args:
            session: The session whose hook asked for presence.
            outbox: Where its presence goes.
        """
        if self._application is not None:
            self._followers[session] = _Follower(outbox)

    def forget(self, session: GameSession) -> None:
        """Stop sending a session's presence.

        Args:
            session: The session.
        """
        self._followers.pop(session, None)

    async def follow_changes(self) -> None:
        """Send the presence that changed once per interval, until cancelled."""
        if self._application is None:
            return
        while True:
            await asyncio.sleep(UPDATE_INTERVAL)
            try:
                self.refresh()
            except Exception:
                logger.exception("Could not send presence")

    def refresh(self) -> None:
        """Send each following session its presence, where it changed since the last."""
        application = self._application
        if application is None or not self._followers:
            return
        hosted = [entry for entry in self._hosting.every() if entry.type == GAME]
        hosts = {entry.host.session for entry in hosted}
        places = _places(hosted)
        for session, follower in self._followers.items():
            activity = None
            try:
                if session not in hosts:
                    activity = self._activity(session, follower, places.get(session))
            except Exception:
                # One player's presence that cannot be built leaves every other player's going.
                if not follower.failing:
                    logger.exception("Could not build a player's presence")
                follower.failing = True
                continue
            follower.failing = False
            update = PresenceUpdate(application=application, activity=activity)
            # Before the first presence there is nothing to clear.
            if update != follower.sent and (follower.sent is not None or activity is not None):
                follower.outbox.send(update)
                follower.sent = update

    def _activity(
        self, session: GameSession, follower: _Follower, place: tuple[HostedGame, Player] | None
    ) -> Activity | None:
        game = session.game
        account = session.account
        if game is None or account is None or account.server:
            return None
        language = language_of(session.locale)
        party = None
        if place is None:
            follower.game_id = None
            details, state = translate("presence.menus", language), None
            started = session.logged_in_at
        else:
            hosted, player = place
            if follower.game_id != hosted.id:
                follower.game_id, follower.since = hosted.id, datetime.now(UTC)
            details = _doing(game, hosted, player, language)
            state = clean_name(hosted.name) or None
            party = Party(size=len(hosted.in_game()), max=hosted.max_players)
            started = follower.since
        image = None
        if self._art.game and game.id in self._icons:
            image = self._art.game.replace("{game}", game.id)
        return Activity(
            name=game.name,
            details=details,
            state=state,
            large_image=image,
            large_text=game.name if image else None,
            small_image=self._art.service,
            small_text=SERVICE if self._art.service else None,
            started_at=int(started.timestamp() * 1000) if started else None,
            party=party,
            page=f"/{game.id}",
        )


def clean_name(name: str) -> str:
    """Return a host's name for its game without addresses, control characters or extra spaces.

    Args:
        name: The name as the host sent it, which may hold the server's address and data center.
    """
    printable = "".join(char if char.isprintable() else " " for char in name).strip()
    if labelled := _LABELLED.fullmatch(printable):
        printable = labelled["name"]
    return " ".join(_ADDRESS.sub(" ", printable).split())


def _places(hosted: list[HostedGame]) -> dict[GameSession, tuple[HostedGame, Player]]:
    # Should a player wait for or play in two games at once, the one it got furthest in counts.
    found: dict[GameSession, tuple[HostedGame, Player]] = {}
    for entry in hosted:
        for player in entry.everyone():
            session = player.connection.session
            known = found.get(session)
            if known is None or known[1].state.value < player.state.value:
                found[session] = (entry, player)
    return found


def _doing(game: Game, hosted: HostedGame, player: Player, language: str) -> str:
    if player.state is PlayerState.QUEUED:
        return translate("presence.queued", language)
    if player.state is not PlayerState.ENTERED:
        return translate("presence.joining", language)
    # Hosts spell the keys in any case.
    attributes = {key.casefold(): value for key, value in (hosted.basic | hosted.details).items()}
    map_name = _named(game, "map", game.listing.map, attributes, language)
    mode = _named(game, "mode", game.listing.mode, attributes, language)
    if map_name and mode:
        return translate("presence.mode_on_map", language).format(mode=mode, map=map_name)
    return map_name or mode or translate("presence.match", language)


def _named(
    game: Game, kind: str, names: Names | None, attributes: Mapping[str, str], language: str
) -> str | None:
    value = attributes.get(names.attribute.casefold()) if names else None
    name = names.name_of(value) if names is not None and value is not None else None
    return translate(f"games.{game.id}.{kind}.{name}", language) if name else None
