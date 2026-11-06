from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, Select, delete, exists, func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
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
from openfesl.core.identities import Identity
from openfesl.core.password_resets import KeptReset
from openfesl.core.verification import Confirmation, Link
from openfesl.storage.admin import action_row
from openfesl.storage.models import (
    AccountRow,
    LoginTokenRow,
    PasswordResetRow,
    PersonaRow,
    ReferralRow,
    SignInIdentityRow,
)

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
        return Credentials(account_from(row), row.password_hash) if row else None

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

    async def account(self, account_id: int) -> Account | None:
        """Return an account by its number.

        Args:
            account_id: The account's number.
        """
        if account_id not in _BIGINT:
            return None
        async with self._sessions() as session:
            row = await session.get(AccountRow, account_id)
        return account_from(row) if row else None

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
            await _change(session, account_id, last_login_at=func.now())

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
        return account_from(row) if row else None

    async def add(
        self,
        nuid: str,
        password_hash: str | None,
        country: str,
        terms_version: str | None,
        *,
        server: bool = False,
        verification: Link | None = None,
        made_in: str | None = None,
        display_name: str | None = None,
        identity: Identity | None = None,
        recruited_by: tuple[str, int] | None = None,
    ) -> Account | None:
        """Create an account, or return None when its name, display name or identity is taken.

        Args:
            nuid: The login name.
            password_hash: The password's hash; None for a server account.
            country: The ISO code chosen on registration.
            terms_version: The version of the terms accepted on registration, if current.
            server: Whether the account belongs to a game's dedicated servers.
            verification: The link that must confirm its e-mail address before it logs in.
            made_in: The id of the game whose client registers it; None for the site.
            display_name: Its public display name, which the site's registration gives it.
            identity: An identity at a sign-in service that becomes a way in with it; nothing is
                made while the identity opens another account.
            recruited_by: The game and the account whose personal link brought the registration,
                which makes the new account its recruit while that account still exists.
        """
        try:
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
                        made_in=made_in,
                        display_name=display_name,
                    )
                    .on_conflict_do_nothing()
                    .returning(AccountRow)
                )
                if row is not None and identity is not None:
                    session.add(
                        SignInIdentityRow(
                            service=identity.service,
                            subject=identity.subject,
                            account_id=row.id,
                            name=identity.name,
                        )
                    )
                    await session.flush()
                if row is not None and recruited_by is not None:
                    game, recruiter = recruited_by
                    found = select(literal(row.id), literal(game), AccountRow.id)
                    await session.execute(
                        insert(ReferralRow).from_select(
                            ["recruit_id", "game", "recruiter_id"],
                            found.where(AccountRow.id == recruiter),
                        )
                    )
                account = account_from(row) if row else None
        except IntegrityError:
            # The identity opens another account, so the transaction made neither.
            return None
        return account

    async def display_name_taken(self, name: str) -> bool:
        """Return whether an account uses a display name, compared regardless of case.

        Args:
            name: The display name.
        """
        async with self._sessions() as session:
            taken = await session.scalar(
                select(exists().where(func.lower(AccountRow.display_name) == func.lower(name)))
            )
        return bool(taken)

    async def name_account(self, account_id: int, name: str) -> bool:
        """Give an account a display name, in place of the one it had, if any.

        Args:
            account_id: The account.
            name: The display name.

        Returns:
            Whether the account has it now, which it has not when another account uses it,
            ignoring case.
        """
        try:
            async with self._sessions.begin() as session:
                await _change(session, account_id, display_name=name)
        except IntegrityError:
            return False
        return True

    async def accept_terms(self, account_id: int, version: str) -> None:
        """Record that an account accepted a version of the terms.

        Args:
            account_id: The account.
            version: The version it accepted.
        """
        async with self._sessions.begin() as session:
            await _change(session, account_id, terms_version=version)

    async def remember(self, account_id: int, token_hash: bytes, password_hash: str | None) -> bool:
        """Keep a login token, dropping the account's oldest beyond the newest few.

        Args:
            account_id: The account.
            token_hash: The SHA-256 digest of the token handed to the game.
            password_hash: The hash the login's password was checked against; None for an
                account without a password.

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
        """Replace an account's password, ending its web sessions and remembered logins.

        Args:
            account_id: The account.
            password_hash: The new password's hash.
        """
        async with self._sessions.begin() as session:
            await _set_password(session, account_id, password_hash)

    async def request_deletion(self, account_id: int) -> datetime:
        """Deactivate an account at its player's request, ending its web sessions, and record it.

        Args:
            account_id: The account.

        Returns:
            When it was deactivated.
        """
        changed = datetime.now(UTC)
        await self._set_state(
            account_id, AccountState.DEACTIVATED, changed, AdminAction.DELETION_REQUEST
        )
        return changed

    async def end_sessions(self, account_id: int) -> None:
        """End an account's web sessions and the logins the games remember for it.

        Args:
            account_id: The account.
        """
        async with self._sessions.begin() as session:
            await _change(session, account_id, session_epoch=AccountRow.session_epoch + 1)
            await session.execute(
                delete(LoginTokenRow).where(LoginTokenRow.account_id == account_id)
            )

    async def remove_password(self, account_id: int, services: Collection[str]) -> bool:
        """Remove an account's password, ending its web sessions and remembered logins.

        Args:
            account_id: The account.
            services: The ids of the configured sign-in services, whose identities alone sign in.

        Returns:
            Whether it was removed, which it is not while no identity at a configured sign-in
            service is linked to the account, as the password is then its last way in.
        """
        async with self._sessions.begin() as session:
            if not await lock_ways_in(session, account_id, services, unlinked=None):
                return False
            await _set_password(session, account_id, None)
        return True

    async def keep_reset(self, account_id: int, link: Link) -> None:
        """Keep a reset link, which replaces the account's earlier one.

        Args:
            account_id: The account whose game password it sets.
            link: The link as the store keeps it.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                delete(PasswordResetRow).where(PasswordResetRow.account_id == account_id)
            )
            session.add(
                PasswordResetRow(
                    token_hash=link.token_hash, account_id=account_id, expires_at=link.expires_at
                )
            )

    async def reset_of(self, token_hash: bytes) -> KeptReset | None:
        """Return the reset link that a token names, without using it; None for an unknown one.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
        """
        async with self._sessions() as session:
            row = await session.get(PasswordResetRow, token_hash)
        return KeptReset(row.account_id, row.expires_at <= datetime.now(UTC)) if row else None

    async def use_reset(self, token_hash: bytes, password_hash: str) -> int | None:
        """Use a working reset link: set its account's game password and confirm its address.

        The account's web sessions and remembered logins end, as with any new password. An
        address that waited for its link loses the identities at sign-in services linked
        meanwhile, which its mailbox never proved.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.
            password_hash: The new game password's hash.

        Returns:
            How many identities it unlinked; None when the link did not work, once used or
            expired.
        """
        async with self._sessions.begin() as session:
            account_id = await session.scalar(
                delete(PasswordResetRow)
                .where(
                    PasswordResetRow.token_hash == token_hash,
                    PasswordResetRow.expires_at > datetime.now(UTC),
                )
                .returning(PasswordResetRow.account_id)
            )
            if account_id is None:
                return None
            waited = await session.scalar(
                select(AccountRow.verification_hash.is_not(None)).where(AccountRow.id == account_id)
            )
            unlinked: Sequence[str] = ()
            if waited:
                # Whoever linked them never proved the mailbox, as a registration with someone
                # else's address and one's own service would not have.
                unlinked = (
                    await session.scalars(
                        delete(SignInIdentityRow)
                        .where(SignInIdentityRow.account_id == account_id)
                        .returning(SignInIdentityRow.service)
                    )
                ).all()
            # The link went to the address, so opening it confirms the address as its own
            # link would.
            await _set_password(
                session,
                account_id,
                password_hash,
                verification_hash=None,
                verification_expires_at=None,
            )
        return len(unlinked)

    async def change_country(self, account_id: int, country: str) -> None:
        """Replace the country an account chose.

        Args:
            account_id: The account.
            country: The ISO code of the new country.
        """
        async with self._sessions.begin() as session:
            await _change(session, account_id, country=country)

    async def set_state(
        self, account_id: int, state: AccountState, until: datetime | None, admin: Admin | None
    ) -> None:
        """Change whether an account may log in, ending its web sessions, and record the change.

        Args:
            account_id: The account.
            state: Its new state.
            until: When the state ends, if it does.
            admin: The admin who changed it; None for the command line.
        """
        await self._set_state(
            account_id, state, func.now(), AdminAction.ACCOUNT_STATE, until, admin
        )

    async def _set_state(
        self,
        account_id: int,
        state: AccountState,
        changed: object,
        action: AdminAction,
        until: datetime | None = None,
        admin: Admin | None = None,
    ) -> None:
        async with self._sessions.begin() as session:
            await _change(
                session,
                account_id,
                state=state.value,
                state_until=until,
                state_changed_at=changed,
                session_epoch=AccountRow.session_epoch + 1,
            )
            details = {"state": state.value, "until": until.isoformat() if until else None}
            session.add(action_row(action, str(account_id), details, admin))

    async def renew(self, account_id: int, link: Link) -> bool:
        """Replace the link of an account once its link expired.

        Args:
            account_id: The account.
            link: The new link.

        Returns:
            Whether the link was replaced, which it is not while the last one works or once
            the address is confirmed.
        """
        expired = AccountRow.verification_expires_at <= datetime.now(UTC)
        return await self._replace_link(account_id, link, expired)

    async def replace(self, account_id: int, link: Link) -> bool:
        """Replace the link of an account that waits for its address to be confirmed.

        Args:
            account_id: The account.
            link: The new link.

        Returns:
            Whether the link was replaced, which it is not once the address is confirmed.
        """
        waiting = AccountRow.verification_expires_at.is_not(None)
        return await self._replace_link(account_id, link, waiting)

    async def _replace_link(
        self, account_id: int, link: Link, condition: ColumnElement[bool]
    ) -> bool:
        async with self._sessions.begin() as session:
            replaced = await session.scalar(
                update(AccountRow)
                .where(AccountRow.id == account_id, condition)
                .values(verification_hash=link.token_hash, verification_expires_at=link.expires_at)
                .returning(AccountRow.id)
            )
        return replaced is not None

    async def waiting(self, token_hash: bytes) -> int | Confirmation:
        """Return the account that waits for a link, without confirming its address.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.

        Returns:
            The account's number, or why the link confirms nothing.
        """
        async with self._sessions() as session:
            return await _waiting(session, token_hash, lock=False)

    async def confirm(self, token_hash: bytes) -> int | Confirmation:
        """Confirm the address of the account that waits for a link.

        Args:
            token_hash: The SHA-256 digest of the token the link carries.

        Returns:
            The account's number, or why nothing was confirmed.
        """
        async with self._sessions.begin() as session:
            found = await _waiting(session, token_hash, lock=True)
            if isinstance(found, int):
                await _change(session, found, verification_hash=None, verification_expires_at=None)
        return found

    async def confirm_account(self, account_id: int, admin: Admin | None) -> None:
        """Confirm an account's address by hand and record it among the admin's actions.

        Args:
            account_id: The account.
            admin: The admin who confirmed it; None for the command line.
        """
        async with self._sessions.begin() as session:
            await _change(session, account_id, verification_hash=None, verification_expires_at=None)
            session.add(action_row(AdminAction.ACCOUNT_VERIFIED, str(account_id), {}, admin))


async def lock_ways_in(
    session: AsyncSession, account_id: int, services: Collection[str], unlinked: str | None
) -> bool:
    """Lock an account's ways in and return whether one stays after a change to them.

    The lock makes concurrent changes to the same account's ways in wait for each other, so
    that two of them cannot together leave it without one.

    Args:
        session: The transaction that changes them.
        account_id: The account.
        services: The ids of the configured sign-in services, whose identities alone sign in.
        unlinked: The service whose identity the change unlinks; None for a change that removes
            the game password.
    """
    password = await session.scalar(
        select(AccountRow.password_hash).where(AccountRow.id == account_id).with_for_update()
    )
    # Unlinking beside the game password, or an identity that signs nothing in, leaves a way in.
    if unlinked is not None and (password is not None or unlinked not in services):
        return True
    others = exists().where(
        SignInIdentityRow.account_id == account_id,
        SignInIdentityRow.service != (unlinked or ""),
        SignInIdentityRow.service.in_(services),
    )
    return bool(await session.scalar(select(others)))


async def _change(session: AsyncSession, account_id: int, **values: object) -> None:
    await session.execute(update(AccountRow).where(AccountRow.id == account_id).values(**values))


async def _set_password(
    session: AsyncSession, account_id: int, password_hash: str | None, **values: object
) -> None:
    await _change(
        session,
        account_id,
        password_hash=password_hash,
        session_epoch=AccountRow.session_epoch + 1,
        **values,
    )
    await session.execute(delete(LoginTokenRow).where(LoginTokenRow.account_id == account_id))


def _is_hex(text: str) -> bool:
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


async def _waiting(session: AsyncSession, token_hash: bytes, lock: bool) -> int | Confirmation:
    statement = select(AccountRow.id, AccountRow.verification_expires_at).where(
        AccountRow.verification_hash == token_hash
    )
    row = (await session.execute(statement.with_for_update() if lock else statement)).one_or_none()
    account_id, expires_at = row or (0, None)
    if expires_at is None:
        return Confirmation.UNKNOWN
    if expires_at <= datetime.now(UTC):
        return Confirmation.EXPIRED
    return account_id


def _record(row: AccountRow) -> AccountRecord:
    return AccountRecord(account_from(row), row.created_at, row.last_login_at)


def account_from(row: AccountRow) -> Account:
    """Return an account as its row keeps it.

    Args:
        row: The row.
    """
    return Account(
        id=row.id,
        nuid=row.nuid,
        country=row.country,
        terms_version=row.terms_version,
        server=row.server,
        state=AccountState(row.state),
        state_until=row.state_until,
        state_changed=row.state_changed_at,
        verification_expires_at=row.verification_expires_at,
        display_name=row.display_name,
        made_in=row.made_in,
        session_epoch=row.session_epoch,
    )
