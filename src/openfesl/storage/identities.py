from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.identities import Identity, LinkedIdentity
from openfesl.storage.models import SignInIdentityRow


class SqlIdentityStore:
    """The identities at sign-in services linked to accounts, in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def account_of(self, service: str, subject: str) -> int | None:
        """Return the account that an identity signs in, if it is linked.

        Args:
            service: The service's id.
            subject: The account's id at the service.
        """
        async with self._sessions() as session:
            account_id: int | None = await session.scalar(
                select(SignInIdentityRow.account_id).where(
                    SignInIdentityRow.service == service, SignInIdentityRow.subject == subject
                )
            )
        return account_id

    async def linked(self, account_id: int) -> list[LinkedIdentity]:
        """Return the identities linked to an account, the oldest first.

        Args:
            account_id: The account.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(SignInIdentityRow)
                .where(SignInIdentityRow.account_id == account_id)
                .order_by(SignInIdentityRow.linked_at)
            )
            return [
                LinkedIdentity(row.service, row.subject, row.name, row.linked_at) for row in rows
            ]

    async def link(self, account_id: int, identity: Identity) -> bool:
        """Link an identity to an account, which it signs in from then on.

        Args:
            account_id: The account.
            identity: The identity.

        Returns:
            Whether it was linked, which it is not when it opens another account or the account
            has an identity of that service already.
        """
        async with self._sessions.begin() as session:
            linked = await session.scalar(
                insert(SignInIdentityRow)
                .values(
                    service=identity.service,
                    subject=identity.subject,
                    account_id=account_id,
                    name=identity.name,
                )
                .on_conflict_do_nothing()
                .returning(SignInIdentityRow.account_id)
            )
        return linked is not None
