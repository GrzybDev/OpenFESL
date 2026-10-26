import re

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.personas import Persona, PersonaRecord, PersonaState
from openfesl.storage.admin import action_row
from openfesl.storage.models import AccountRow, PersonaRow

_LIKE_SPECIAL = re.compile(r"[\\%_]")
_BIGINT = range(-(2**63), 2**63)


class SqlPersonaStore:
    """Personas in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def of_account(self, account_id: int, namespace: str) -> list[Persona]:
        """Return an account's enabled personas in a namespace, oldest first.

        Args:
            account_id: The account.
            namespace: The namespace.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PersonaRow)
                .where(
                    PersonaRow.account_id == account_id,
                    PersonaRow.namespace == namespace,
                    PersonaRow.disabled_at.is_(None),
                )
                .order_by(PersonaRow.id)
            )
            return [_persona(row) for row in rows]

    async def records(self, account_ids: list[int]) -> dict[int, list[PersonaRecord]]:
        """Return the personas of some accounts in every namespace, oldest first, by account.

        Those their players removed are included.

        Args:
            account_ids: The accounts.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PersonaRow)
                .where(PersonaRow.account_id.in_(account_ids))
                .order_by(PersonaRow.id)
            )
            found: dict[int, list[PersonaRecord]] = {account_id: [] for account_id in account_ids}
            for row in rows:
                found[row.account_id].append(
                    PersonaRecord(
                        _persona(row), row.created_at, row.disabled_at, PersonaState(row.state)
                    )
                )
            return found

    async def find(self, namespace: str, name: str) -> Persona | None:
        """Return the enabled persona with a name, matched regardless of case.

        Args:
            namespace: The namespace.
            name: The name.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(PersonaRow).where(
                    PersonaRow.namespace == namespace,
                    func.lower(PersonaRow.name) == func.lower(name),
                    PersonaRow.disabled_at.is_(None),
                )
            )
        return _persona(row) if row else None

    async def find_by_id(self, persona_id: int) -> Persona | None:
        """Return an enabled persona by its number.

        Args:
            persona_id: The persona's number.
        """
        # Games send any number they like, and the driver refuses those beyond a bigint.
        if persona_id not in _BIGINT:
            return None
        async with self._sessions() as session:
            row = await session.get(PersonaRow, persona_id)
        return _persona(row) if row and row.disabled_at is None else None

    async def search(self, namespace: str, pattern: str, limit: int) -> list[Persona]:
        """Return players' enabled personas whose names match a pattern regardless of case.

        Args:
            namespace: The namespace.
            pattern: The name, where * stands for any run of characters.
            limit: The most personas to return, by name.
        """
        like = _LIKE_SPECIAL.sub(lambda match: "\\" + match[0], pattern).replace("*", "%")
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PersonaRow)
                .join(AccountRow, AccountRow.id == PersonaRow.account_id)
                .where(
                    PersonaRow.namespace == namespace,
                    PersonaRow.name.ilike(like, escape="\\"),
                    PersonaRow.disabled_at.is_(None),
                    AccountRow.server.is_(False),
                )
                .order_by(func.lower(PersonaRow.name))
                .limit(limit)
            )
            return [_persona(row) for row in rows]

    async def add(self, account_id: int, namespace: str, name: str) -> Persona | None:
        """Create a persona, or return None when the name is taken in the namespace.

        Args:
            account_id: The account it belongs to.
            namespace: The namespace.
            name: The name.
        """
        async with self._sessions.begin() as session:
            row = await session.scalar(
                insert(PersonaRow)
                .values(account_id=account_id, namespace=namespace, name=name)
                .on_conflict_do_nothing()
                .returning(PersonaRow)
            )
            return _persona(row) if row else None

    async def disable(self, persona_id: int) -> None:
        """Take a persona out of use; its name stays taken.

        Args:
            persona_id: The persona's number.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(PersonaRow).where(PersonaRow.id == persona_id).values(disabled_at=func.now())
            )

    async def named(self, name: str) -> list[Persona]:
        """Return players' personas with a name in every namespace, those they disabled too.

        Args:
            name: The name, matched regardless of case.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(PersonaRow)
                .join(AccountRow, AccountRow.id == PersonaRow.account_id)
                .where(
                    func.lower(PersonaRow.name) == func.lower(name), AccountRow.server.is_(False)
                )
                .order_by(PersonaRow.namespace)
            )
            return [_persona(row) for row in rows]

    async def blocked(self, namespace: str, name: str) -> bool:
        """Return whether the admin disabled the persona with a name.

        Args:
            namespace: The namespace.
            name: The name, matched regardless of case.
        """
        async with self._sessions() as session:
            state = await session.scalar(
                select(PersonaRow.state).where(
                    PersonaRow.namespace == namespace,
                    func.lower(PersonaRow.name) == func.lower(name),
                )
            )
        return state == PersonaState.DISABLED

    async def set_state(self, persona_id: int, state: PersonaState, admin: Admin | None) -> None:
        """Change whether a persona may play and record the change among the admin's actions.

        Args:
            persona_id: The persona's number.
            state: Its new state.
            admin: The admin who changed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(PersonaRow)
                .where(PersonaRow.id == persona_id)
                .values(state=state.value, state_changed_at=func.now())
            )
            details = {"state": state.value}
            session.add(action_row(AdminAction.PERSONA_STATE, str(persona_id), details, admin))


def _persona(row: PersonaRow) -> Persona:
    return Persona(id=row.id, account_id=row.account_id, namespace=row.namespace, name=row.name)
