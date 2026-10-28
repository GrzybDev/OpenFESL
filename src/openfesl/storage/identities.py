from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.identities import Identity, LinkedIdentity
from openfesl.core.proofs import KeptProof
from openfesl.core.verification import Link
from openfesl.storage.models import SignInIdentityRow, SignInProofRow


class SqlIdentityStore:
    """The identities at sign-in services linked to accounts and the proof links, in PostgreSQL."""

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

    async def keep_proof(self, account_id: int, identity: Identity, link: Link) -> None:
        """Keep a proof link, which replaces the account's earlier ones for the same service.

        Args:
            account_id: The account it proves.
            identity: The identity it links to the account.
            link: The link as the store keeps it.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                delete(SignInProofRow).where(
                    SignInProofRow.account_id == account_id,
                    SignInProofRow.service == identity.service,
                )
            )
            session.add(
                SignInProofRow(
                    token_hash=link.token_hash,
                    account_id=account_id,
                    service=identity.service,
                    subject=identity.subject,
                    name=identity.name,
                    expires_at=link.expires_at,
                )
            )

    async def proof_of(self, token_hash: bytes) -> KeptProof | None:
        """Return the proof link that a token names, without using it; None for an unknown one.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """
        async with self._sessions() as session:
            row = await session.get(SignInProofRow, token_hash)
        return _kept(row) if row else None

    async def take_proof(self, token_hash: bytes) -> KeptProof | None:
        """Take a proof link out of use and return it; None for one the store does not keep.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """
        async with self._sessions.begin() as session:
            row = await session.scalar(
                delete(SignInProofRow)
                .where(SignInProofRow.token_hash == token_hash)
                .returning(SignInProofRow)
            )
            # Before the commit, which expires the row's attributes.
            return _kept(row) if row else None


def _kept(row: SignInProofRow) -> KeptProof:
    identity = Identity(row.service, row.subject, row.name, None)
    return KeptProof(row.account_id, identity, row.expires_at <= datetime.now(UTC))
