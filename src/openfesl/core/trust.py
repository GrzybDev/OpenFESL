import asyncio
import logging
from typing import TYPE_CHECKING

from openfesl.core.hosted_games import HostedGame
from openfesl.core.hosting import GAME, Hosting
from openfesl.core.servers import Approval, Standing

if TYPE_CHECKING:
    from openfesl.core.session import GameSession

logger = logging.getLogger(__name__)

# The admin decides in another process, so the server reads the decisions back this often.
APPROVAL_CHECK_INTERVAL = 5.0


class ServerTrust:
    """What the admin's decisions let dedicated servers do: list games and keep players' data."""

    def __init__(self, hosting: Hosting, approval: Approval) -> None:
        """Trust the dedicated servers whose games the admin lets players see.

        Args:
            hosting: The games hosts list.
            approval: Decides who sees the games of dedicated servers.
        """
        self._hosting = hosting
        self._approval = approval

    async def admits(self, session: GameSession) -> bool:
        """Return whether a session hosts a game that players see as of now.

        The servers of a game share their FESL login, so only this tells a dedicated server
        that keeps its players' stats and lists from anyone who logs in like one.

        Args:
            session: The session.
        """
        hosted = self._served_by(session)
        return hosted is not None and await self._listed(hosted)

    async def admits_player(self, session: GameSession, account_id: int) -> bool:
        """Return whether a session hosts a game that players see as of now, with a player in it.

        Args:
            session: The session.
            account_id: The player's account.
        """
        hosted = self._served_by(session)
        if hosted is None or all(
            player.persona.account_id != account_id for player in hosted.players.values()
        ):
            return False
        return await self._listed(hosted)

    def _served_by(self, session: GameSession) -> HostedGame | None:
        return next(
            (
                hosted
                for hosted in self._hosting.every()
                if hosted.type == GAME and hosted.server and hosted.host.session is session
            ),
            None,
        )

    async def _listed(self, hosted: HostedGame) -> bool:
        # The admin may have decided otherwise since the game listed.
        standings = await self._approval.standings([hosted.ugid])
        return standings.get(hosted.ugid) is Standing.LISTED

    async def follow_approvals(self) -> None:
        """Apply the admin's decisions to the games of dedicated servers until cancelled."""
        while True:
            await asyncio.sleep(APPROVAL_CHECK_INTERVAL)
            try:
                await self.apply_decisions()
            except Exception:
                # The database may be away for a moment; the next check tries again.
                logger.exception("Could not read the admin's decisions about hosting servers")

    async def apply_decisions(self) -> None:
        """List, hide or end the games of dedicated servers as the admin last decided."""
        vetted = [hosted for hosted in self._hosting.every() if hosted.server is not None]
        if not vetted:
            return
        standings = await self._approval.standings({hosted.ugid for hosted in vetted})
        for hosted in vetted:
            standing = standings.get(hosted.ugid, Standing.REFUSED)
            listed = standing is Standing.LISTED
            if standing is Standing.REFUSED:
                self._hosting.end(hosted)
            elif hosted.listed != listed:
                hosted.listed = listed
                logger.info("Game %d %s", hosted.id, "listed" if listed else "hidden")
