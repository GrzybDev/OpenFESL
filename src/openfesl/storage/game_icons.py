from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.game_icons import StoredIcon
from openfesl.storage.admin import action_row
from openfesl.storage.models import AdminActionRow, GameIconRow


class SqlGameIconStore:
    """The games' icons in PostgreSQL, each change recorded among the admin's actions."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def games(self) -> set[str]:
        """Return the ids of the games that have an icon."""
        async with self._sessions() as session:
            return set(await session.scalars(select(GameIconRow.game)))

    async def icon(self, game: str) -> bytes | None:
        """Return a game's icon, if it has one.

        Args:
            game: The game's id.
        """
        async with self._sessions() as session:
            return await session.scalar(select(GameIconRow.png).where(GameIconRow.game == game))

    async def stored(self) -> dict[str, StoredIcon]:
        """Return each game's icon with when it was stored and by whom, by the game's id."""
        # The latest change of each game's icon is the one that stored the icon it has.
        latest = (
            select(AdminActionRow.target, func.max(AdminActionRow.id).label("id"))
            .where(AdminActionRow.action == AdminAction.GAME_ICON)
            .group_by(AdminActionRow.target)
            .subquery()
        )
        async with self._sessions() as session:
            rows = await session.execute(
                select(
                    GameIconRow.game, GameIconRow.png, AdminActionRow.at, AdminActionRow.admin_name
                )
                .outerjoin(latest, latest.c.target == GameIconRow.game)
                .outerjoin(AdminActionRow, AdminActionRow.id == latest.c.id)
            )
            return {game: StoredIcon(png, at, by) for game, png, at, by in rows}

    async def store(self, game: str, png: bytes, admin: Admin | None) -> None:
        """Store a game's icon in place of the one it had, and record the change.

        Args:
            game: The game's id.
            png: The icon as a PNG image.
            admin: The admin who stored it; None for the command line.
        """
        async with self._sessions.begin() as session:
            statement = insert(GameIconRow).values(game=game, png=png)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[GameIconRow.game], set_={"png": statement.excluded.png}
                )
            )
            session.add(action_row(AdminAction.GAME_ICON, game, {"stored": True}, admin))

    async def remove(self, game: str, admin: Admin | None) -> bool:
        """Remove a game's icon and record the change.

        Args:
            game: The game's id.
            admin: The admin who removed it; None for the command line.

        Returns:
            Whether the game had an icon.
        """
        async with self._sessions.begin() as session:
            removed = await session.scalar(
                delete(GameIconRow).where(GameIconRow.game == game).returning(GameIconRow.game)
            )
            if removed is not None:
                session.add(action_row(AdminAction.GAME_ICON, game, {"stored": False}, admin))
        return removed is not None
