from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
