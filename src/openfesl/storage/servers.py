from collections.abc import Collection
from dataclasses import replace

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.servers import (
    Decision,
    GameServer,
    HostingMode,
    Provider,
    ProviderHistory,
    ServerState,
)
from openfesl.storage.admin import action_row
from openfesl.storage.models import AdminActionRow, GameServerRow, ProviderRow, SettingRow

HOSTING_MODE = "hosting_mode"


class SqlServerStore:
    """The hosting mode, the providers and the dedicated servers in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def hosting_mode(self) -> HostingMode:
        """Return whose games players see; only approved servers' until the admin opens hosting."""
        async with self._sessions() as session:
            value = await session.scalar(
                select(SettingRow.value).where(SettingRow.key == HOSTING_MODE)
            )
        return HostingMode(value) if value else HostingMode.APPROVED

    async def set_hosting_mode(self, mode: HostingMode) -> None:
        """Change whose games players see.

        Args:
            mode: The new mode.
        """
        statement = insert(SettingRow).values(key=HOSTING_MODE, value=mode.value)
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[SettingRow.key], set_={"value": statement.excluded.value}
                )
            )

    async def add_provider(self, provider: Provider, admin: Admin | None) -> bool:
        """Keep a new provider, unless another has its name, recording who added it.

        Args:
            provider: The provider, with an id no other provider has.
            admin: The admin who added it; None for the command line.

        Returns:
            Whether it was kept: names are unique, compared without case.
        """
        async with self._sessions.begin() as session:
            taken = await session.scalar(
                select(ProviderRow.id).where(
                    func.lower(ProviderRow.name) == provider.name.casefold()
                )
            )
            if taken is not None:
                return False
            session.add(ProviderRow(id=provider.id, name=provider.name, revoked=provider.revoked))
            details = {"name": provider.name}
            session.add(action_row(AdminAction.PROVIDER_ADD, provider.id.hex(), details, admin))
        return True

    async def providers(self) -> list[Provider]:
        """Return every provider, oldest first."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ProviderRow).order_by(ProviderRow.created_at, ProviderRow.id)
            )
            return [_provider(row) for row in rows]

    async def revoke_provider(self, provider_id: bytes, admin: Admin | None) -> bool:
        """Revoke a provider, which revokes every server that reports it, recording who did.

        Args:
            provider_id: The provider's id.
            admin: The admin who revoked it; None for the command line.

        Returns:
            Whether a provider has that id.
        """
        async with self._sessions.begin() as session:
            changed = await session.scalar(
                update(ProviderRow)
                .where(ProviderRow.id == provider_id)
                .values(revoked=True)
                .returning(ProviderRow.id)
            )
            if changed is not None:
                action = AdminAction.PROVIDER_REVOKE
                session.add(action_row(action, provider_id.hex(), {}, admin))
        return changed is not None

    async def provider_history(self) -> dict[bytes, ProviderHistory]:
        """Return when each provider was added and revoked, and by whom, by its id."""
        actions = (AdminAction.PROVIDER_ADD.value, AdminAction.PROVIDER_REVOKE.value)
        async with self._sessions() as session:
            providers = await session.execute(select(ProviderRow.id, ProviderRow.created_at))
            history = {id: ProviderHistory(added) for id, added in providers}
            rows = await session.scalars(
                select(AdminActionRow)
                .where(AdminActionRow.action.in_(actions))
                .order_by(AdminActionRow.id)
            )
            for row in rows:
                provider_id = bytes.fromhex(row.target)
                if (found := history.get(provider_id)) is None:
                    continue
                if row.action == AdminAction.PROVIDER_ADD:
                    history[provider_id] = replace(found, added_by=row.admin_name)
                else:
                    history[provider_id] = replace(found, revoked=row.at, revoked_by=row.admin_name)
        return history

    async def add(self, server: GameServer) -> None:
        """Keep a new server.

        Args:
            server: The server, with an id no other server has.
        """
        async with self._sessions.begin() as session:
            session.add(
                GameServerRow(
                    ugid=server.ugid,
                    secret_hash=server.secret_hash,
                    name=server.name,
                    state=server.state.value,
                    provider_id=server.provider_id,
                )
            )

    async def find(self, ugids: Collection[str]) -> list[GameServer]:
        """Return the servers with some persistent game ids, each with its known provider.

        Args:
            ugids: The ids.
        """
        return await self._servers(GameServerRow.ugid.in_(ugids))

    async def attach(self, ugid: str, provider_id: bytes) -> GameServer | None:
        """Record the provider id that a server's games report.

        Args:
            ugid: The server's persistent game id.
            provider_id: The provider id.

        Returns:
            The server as it is now, or None when no server has that id.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(GameServerRow)
                .where(GameServerRow.ugid == ugid)
                .values(provider_id=provider_id)
            )
        found = await self.find([ugid])
        return found[0] if found else None

    async def set_state(self, ugid: str, state: ServerState, admin: Admin | None) -> bool:
        """Approve or revoke a server, recording the decision among the admin's actions.

        Args:
            ugid: The server's persistent game id.
            state: The admin's decision.
            admin: The admin who decided; None for the command line.

        Returns:
            Whether a server has that id.
        """
        async with self._sessions.begin() as session:
            changed = await session.scalar(
                update(GameServerRow)
                .where(GameServerRow.ugid == ugid)
                .values(state=state.value)
                .returning(GameServerRow.ugid)
            )
            if changed is not None:
                details = {"state": state.value}
                session.add(action_row(AdminAction.SERVER_STATE, ugid, details, admin))
        return changed is not None

    async def decisions(self, ugids: Collection[str]) -> dict[str, Decision]:
        """Return the admin's latest decision about each of some servers that has one.

        Args:
            ugids: The servers' persistent game ids.
        """
        statement = (
            select(AdminActionRow)
            .where(
                AdminActionRow.action == AdminAction.SERVER_STATE.value,
                AdminActionRow.target.in_(list(ugids)),
            )
            .order_by(AdminActionRow.id)
        )
        async with self._sessions() as session:
            rows = await session.scalars(statement)
            return {
                row.target: Decision(ServerState(row.details["state"]), row.at, row.admin_name)
                for row in rows
            }

    async def all(self) -> list[GameServer]:
        """Return every server that ever hosted, oldest first."""
        return await self._servers()

    async def _servers(self, *conditions: ColumnElement[bool]) -> list[GameServer]:
        statement = (
            select(GameServerRow, ProviderRow)
            .outerjoin(ProviderRow, ProviderRow.id == GameServerRow.provider_id)
            .where(*conditions)
            .order_by(GameServerRow.created_at, GameServerRow.ugid)
        )
        async with self._sessions() as session:
            rows = await session.execute(statement)
            return [_server(server, provider) for server, provider in rows]


def _provider(row: ProviderRow) -> Provider:
    return Provider(row.id, row.name, row.revoked)


def _server(row: GameServerRow, provider: ProviderRow | None) -> GameServer:
    return GameServer(
        ugid=row.ugid,
        secret_hash=row.secret_hash,
        name=row.name,
        state=ServerState(row.state),
        provider_id=row.provider_id,
        provider=_provider(provider) if provider else None,
        first_seen=row.created_at,
    )
