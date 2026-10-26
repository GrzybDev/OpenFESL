from collections.abc import Collection
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from openfesl.core.accounts import (
    Account,
    AccountRecord,
    AccountState,
    Credentials,
    RememberedLogin,
)
from openfesl.core.admin import AdminAction
from openfesl.core.admins import Admin
from openfesl.core.verification import Confirmation, Link
from openfesl.storage.admin import action_row
from openfesl.storage.models import AccountRow, LoginTokenRow, PersonaRow

REMEMBERED_LOGINS = 8
REMEMBERED_FOR = timedelta(days=180)
# Account numbers in addresses can be any number, and the driver refuses those beyond a bigint.
_BIGINT = range(-(2**63), 2**63)


class SqlAccountStore:
    """Accounts and remembered logins in PostgreSQL."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        """Use a database through its session factory.

        Args:
            sessions: Opens database sessions.
        """
        self._sessions = sessions

    async def find(self, nuid: str) -> Credentials | None:
        """Return the account that logs in with a name, matched regardless of case.

        Args:
            nuid: The login name.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(AccountRow).where(func.lower(AccountRow.nuid) == func.lower(nuid))
            )
        return Credentials(_account(row), row.password_hash) if row else None

    async def totals(self, since: datetime) -> tuple[int, int]:
        """Return how many players' accounts there are, and how many of them are new.

        Args:
            since: Accounts created from then on count as new.
        """
        new = func.count().filter(AccountRow.created_at >= since)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(func.count(), new).select_from(AccountRow).where(~AccountRow.server)
                )
            ).one()
            return int(row[0]), int(row[1])

    async def search(
        self, start: str, oldest_first: bool, offset: int, limit: int
    ) -> tuple[int, list[AccountRecord]]:
        """Return the players' accounts whose login name or a persona's name starts with a text.

        Args:
            start: The start of the name, matched regardless of case; empty matches every
                account.
            oldest_first: Whether the oldest accounts come first; otherwise the newest do.
            offset: How many matching accounts to skip.
            limit: The most accounts to return.

        Returns:
            How many accounts match, and those of the page.
        """
        matching: Select[AccountRow] = select(AccountRow).where(~AccountRow.server)
        if start:
            named = exists().where(
                PersonaRow.account_id == AccountRow.id,
                PersonaRow.name.istartswith(start, autoescape=True),
            )
            matching = matching.where(AccountRow.nuid.istartswith(start, autoescape=True) | named)
        order = (AccountRow.created_at, AccountRow.id)
        async with self._sessions() as session:
            total = await session.scalar(select(func.count()).select_from(matching.subquery()))
            rows = await session.scalars(
                matching.order_by(*(order if oldest_first else (column.desc() for column in order)))
                .offset(offset)
                .limit(limit)
            )
            return total or 0, [_record(row) for row in rows]

    async def record(self, account_id: int) -> AccountRecord | None:
        """Return a player's account by its number.

        Args:
            account_id: The account's number.
        """
        if account_id not in _BIGINT:
            return None
        async with self._sessions() as session:
            row = await session.get(AccountRow, account_id)
        return _record(row) if row and not row.server else None

    async def names(self, account_ids: Collection[int]) -> dict[int, str]:
        """Return the login names of some accounts, by number.

        Args:
            account_ids: The accounts.
        """
        wanted = [number for number in account_ids if number in _BIGINT]
        async with self._sessions() as session:
            rows = await session.execute(
                select(AccountRow.id, AccountRow.nuid).where(AccountRow.id.in_(wanted))
            )
            return {number: nuid for number, nuid in rows}

    async def remembered(self, account_id: int) -> list[RememberedLogin]:
        """Return the logins remembered for an account that still log it in, newest first.

        Args:
            account_id: The account.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(LoginTokenRow.token_hash, LoginTokenRow.created_at)
                .where(
                    LoginTokenRow.account_id == account_id,
                    LoginTokenRow.created_at > func.now() - REMEMBERED_FOR,
                )
                .order_by(LoginTokenRow.created_at.desc())
            )
            return [RememberedLogin(token.hex(), created) for token, created in rows]

    async def forget(self, account_id: int, login_ids: list[str] | None, admin: Admin) -> int:
        """Remove logins that the game remembers for an account and record it.

        Args:
            account_id: The account.
            login_ids: The logins by their ids; None removes every one.
            admin: The admin who removed them.

        Returns:
            How many were removed.
        """
        statement = delete(LoginTokenRow).where(LoginTokenRow.account_id == account_id)
        if login_ids is not None:
            hashes = [bytes.fromhex(login) for login in login_ids if _is_hex(login)]
            statement = statement.where(LoginTokenRow.token_hash.in_(hashes))
        async with self._sessions.begin() as session:
            found = await session.execute(statement.returning(LoginTokenRow.token_hash))
            removed = len(found.all())
            if removed:
                session.add(
                    action_row(
                        AdminAction.LOGINS_FORGET, str(account_id), {"count": removed}, admin
                    )
                )
            return removed

    async def record_login(self, account_id: int) -> None:
        """Note that a player's account logged in now.

        Args:
            account_id: The account.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(AccountRow)
                .where(AccountRow.id == account_id)
                .values(last_login_at=func.now())
            )

    async def find_remembered(self, token_hash: bytes) -> Account | None:
        """Return the account that a remembered login belongs to, if it is recent enough.

        Args:
            token_hash: The SHA-256 digest of the token the game kept.
        """
        async with self._sessions() as session:
            row = await session.scalar(
                select(AccountRow)
                .join(LoginTokenRow, LoginTokenRow.account_id == AccountRow.id)
                .where(
                    LoginTokenRow.token_hash == token_hash,
                    LoginTokenRow.created_at > func.now() - REMEMBERED_FOR,
                )
            )
        return _account(row) if row else None

    async def add(
        self,
        nuid: str,
        password_hash: str | None,
        country: str,
        terms_version: str | None,
        *,
        server: bool = False,
        verification: Link | None = None,
    ) -> Account | None:
        """Create an account, or return None when the name is taken.

        Args:
            nuid: The login name.
            password_hash: The password's hash; None for a server account.
            country: The ISO code chosen on registration.
            terms_version: The version of the terms accepted on registration, if current.
            server: Whether the account belongs to a game's dedicated servers.
            verification: The link that must confirm its e-mail address before it logs in.
        """
        async with self._sessions.begin() as session:
            row = await session.scalar(
                insert(AccountRow)
                .values(
                    nuid=nuid,
                    password_hash=password_hash,
                    country=country,
                    terms_version=terms_version,
                    server=server,
                    verification_hash=verification.token_hash if verification else None,
                    verification_expires_at=verification.expires_at if verification else None,
                )
                .on_conflict_do_nothing()
                .returning(AccountRow)
            )
            return _account(row) if row else None

    async def accept_terms(self, account_id: int, version: str) -> None:
        """Record that an account accepted a version of the terms.

        Args:
            account_id: The account.
            version: The version it accepted.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(AccountRow).where(AccountRow.id == account_id).values(terms_version=version)
            )

    async def remember(self, account_id: int, token_hash: bytes, password_hash: str) -> bool:
        """Keep a login token, dropping the account's oldest beyond the newest few.

        Args:
            account_id: The account.
            token_hash: The SHA-256 digest of the token handed to the game.
            password_hash: The hash the login's password was checked against.

        Returns:
            Whether the token was kept, which it is not once the password changed.
        """
        async with self._sessions.begin() as session:
            # The lock waits for a password change under way, which forgets the tokens it finds,
            # and a later change waits for this token, so neither misses the other.
            current = await session.scalar(
                select(AccountRow.password_hash)
                .where(AccountRow.id == account_id)
                .with_for_update(read=True)
            )
            if current != password_hash:
                return False
            session.add(LoginTokenRow(token_hash=token_hash, account_id=account_id))
            await session.flush()
            newest = (
                select(LoginTokenRow.token_hash)
                .where(LoginTokenRow.account_id == account_id)
                .order_by(LoginTokenRow.created_at.desc())
                .limit(REMEMBERED_LOGINS)
            )
            await session.execute(
                delete(LoginTokenRow).where(
                    LoginTokenRow.account_id == account_id,
                    LoginTokenRow.token_hash.not_in(newest),
                )
            )
        return True

    async def change_password(self, account_id: int, password_hash: str) -> None:
        """Replace an account's password and forget every login remembered with the old one.

        Args:
            account_id: The account.
            password_hash: The new password's hash.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(AccountRow)
                .where(AccountRow.id == account_id)
                .values(password_hash=password_hash)
            )
            await session.execute(
                delete(LoginTokenRow).where(LoginTokenRow.account_id == account_id)
            )

    async def change_country(self, account_id: int, country: str) -> None:
        """Replace the country an account chose.

        Args:
            account_id: The account.
            country: The ISO code of the new country.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(AccountRow).where(AccountRow.id == account_id).values(country=country)
            )

    async def set_state(
        self, account_id: int, state: AccountState, until: datetime | None, admin: Admin | None
    ) -> None:
        """Change whether an account may log in and record the change among the admin's actions.

        Args:
            account_id: The account.
            state: Its new state.
            until: When the state ends, if it does.
            admin: The admin who changed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(AccountRow)
                .where(AccountRow.id == account_id)
                .values(state=state.value, state_until=until, state_changed_at=func.now())
            )
            details = {"state": state.value, "until": until.isoformat() if until else None}
            session.add(action_row(AdminAction.ACCOUNT_STATE, str(account_id), details, admin))

    async def renew(self, account_id: int, link: Link) -> bool:
        """Replace the link of an account once its link expired.

        Args:
            account_id: The account.
            link: The new link.

        Returns:
            Whether the link was replaced, which it is not while the last one works or once
            the address is confirmed.
        """
        async with self._sessions.begin() as session:
            renewed = await session.scalar(
                update(AccountRow)
                .where(
                    AccountRow.id == account_id,
                    AccountRow.verification_expires_at <= datetime.now(UTC),
                )
                .values(verification_hash=link.token_hash, verification_expires_at=link.expires_at)
                .returning(AccountRow.id)
            )
        return renewed is not None

    async def confirm(self, token_hash: bytes) -> Confirmation:
        """Confirm the address of the account that waits for a link.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """
        async with self._sessions.begin() as session:
            expires_at = await session.scalar(
                select(AccountRow.verification_expires_at)
                .where(AccountRow.verification_hash == token_hash)
                .with_for_update()
            )
            if expires_at is None:
                return Confirmation.UNKNOWN
            if expires_at <= datetime.now(UTC):
                return Confirmation.EXPIRED
            await session.execute(
                update(AccountRow)
                .where(AccountRow.verification_hash == token_hash)
                .values(verification_hash=None, verification_expires_at=None)
            )
        return Confirmation.CONFIRMED

    async def confirm_account(self, account_id: int, admin: Admin | None) -> None:
        """Confirm an account's address by hand and record it among the admin's actions.

        Args:
            account_id: The account.
            admin: The admin who confirmed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                update(AccountRow)
                .where(AccountRow.id == account_id)
                .values(verification_hash=None, verification_expires_at=None)
            )
            session.add(action_row(AdminAction.ACCOUNT_VERIFIED, str(account_id), {}, admin))


def _is_hex(text: str) -> bool:
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


def _record(row: AccountRow) -> AccountRecord:
    return AccountRecord(_account(row), row.created_at, row.last_login_at, row.state_changed_at)


def _account(row: AccountRow) -> Account:
    return Account(
        id=row.id,
        nuid=row.nuid,
        country=row.country,
        terms_version=row.terms_version,
        server=row.server,
        state=AccountState(row.state),
        state_until=row.state_until,
        verification_expires_at=row.verification_expires_at,
    )
