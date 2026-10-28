from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    SmallInteger,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AccountRow(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    nuid: Mapped[str]
    password_hash: Mapped[str | None]
    country: Mapped[str]
    terms_version: Mapped[str | None]
    server: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    state: Mapped[str] = mapped_column(server_default="active")
    state_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set while the account waits for the link that confirms its e-mail address.
    verification_hash: Mapped[bytes | None] = mapped_column(LargeBinary, unique=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Moved on whenever the account's web sessions must end, such as on a password change.
    session_epoch: Mapped[int] = mapped_column(server_default="0")
    display_name: Mapped[str | None]
    # The game whose client registered the account; none for the site's own registrations.
    made_in: Mapped[str | None]


Index("account_nuid_key", func.lower(AccountRow.nuid), unique=True)
Index("account_display_name_key", func.lower(AccountRow.display_name), unique=True)


class LoginTokenRow(Base):
    __tablename__ = "login_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SignInIdentityRow(Base):
    __tablename__ = "sign_in_identity"
    # One identity of each service per account, and each identity opens one account.
    __table_args__ = (UniqueConstraint("account_id", "service"),)

    service: Mapped[str] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    name: Mapped[str]
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PersonaRow(Base):
    __tablename__ = "persona"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    namespace: Mapped[str]
    name: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # When its player removed it; the admin's decision is the state.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(server_default="active")
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Disabled names stay taken, so a name never moves to another account.
Index("persona_name_key", PersonaRow.namespace, func.lower(PersonaRow.name), unique=True)


class EntitlementRow(Base):
    __tablename__ = "entitlement"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    # The persona that owns it, for a game whose personas own items each; none for the
    # account's own.
    persona_id: Mapped[int | None] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"))
    tag: Mapped[str]
    group_name: Mapped[str | None]
    product_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="ACTIVE")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    use_count: Mapped[int | None]
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# One row per tag of an account, so a key entered again grants nothing twice and a revoked tag
# stays so.
Index(
    "entitlement_tag_key",
    EntitlementRow.account_id,
    EntitlementRow.tag,
    unique=True,
    postgresql_where=EntitlementRow.persona_id.is_(None),
)
# One row per item of a persona, named before the first '-' of its Magma tag, since a game keeps
# one row of each item and ignores the others.
Index(
    "entitlement_item_key",
    EntitlementRow.persona_id,
    func.split_part(EntitlementRow.tag, "-", 1),
    unique=True,
    postgresql_where=EntitlementRow.persona_id.is_not(None),
)


class WalletRow(Base):
    __tablename__ = "wallet"
    # An account's balance in a currency of a game that its personas share, or a persona's own.
    __table_args__ = (
        UniqueConstraint(
            "account_id", "game", "currency", "persona_id", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    game: Mapped[str]
    currency: Mapped[str]
    persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), index=True
    )
    # The sum of the wallet's ledger, changed only together with an entry in it.
    balance: Mapped[int] = mapped_column(BigInteger)


class WalletEntryRow(Base):
    __tablename__ = "wallet_entry"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id", ondelete="CASCADE"), index=True)
    delta: Mapped[int] = mapped_column(BigInteger)
    # The wallet's balance after the change.
    balance: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductKeyRow(Base):
    __tablename__ = "product_key"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # How many accounts may redeem it; None for any number.
    max_uses: Mapped[int | None]
    once_per_account: Mapped[bool] = mapped_column(server_default="false")
    # What the public pages call the key, by language.
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, server_default="{}")
    # The game a key added by the admin is for; starting keys belong to their game data's.
    game: Mapped[str | None]


Index("product_key_code_key", func.upper(ProductKeyRow.code), unique=True)


class ProductKeyUseRow(Base):
    __tablename__ = "product_key_use"

    key_id: Mapped[int] = mapped_column(
        ForeignKey("product_key.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductKeyGrantRow(Base):
    __tablename__ = "product_key_grant"

    key_id: Mapped[int] = mapped_column(
        ForeignKey("product_key.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(primary_key=True)
    group_name: Mapped[str | None]
    product_id: Mapped[str | None]


class StatRow(Base):
    __tablename__ = "stat"

    partition: Mapped[str] = mapped_column(primary_key=True)
    owner_type: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    period_start: Mapped[date] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[float] = mapped_column(Double)
    text: Mapped[str]


# Leaderboards order everyone holding a key in a period by value.
Index(
    "stat_leaderboard",
    StatRow.partition,
    StatRow.owner_type,
    StatRow.key,
    StatRow.period,
    StatRow.period_start,
    StatRow.value,
)


class RecordEntryRow(Base):
    __tablename__ = "record_entry"

    # The persona first, so the key also serves deleting a persona's records with it.
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), primary_key=True
    )
    partition: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AssociationRow(Base):
    __tablename__ = "association"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), primary_key=True
    )
    type: Mapped[str] = mapped_column(primary_key=True)
    # `/domain/subDomain[/key]` as the game names the list, empty for lists without one.
    partition: Mapped[str] = mapped_column(primary_key=True)
    member_id: Mapped[int] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    modified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MessageRow(Base):
    __tablename__ = "message"

    # The SDK reads message numbers as 32-bit integers.
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("persona.id", ondelete="CASCADE"), index=True)
    type: Mapped[str]
    purge: Mapped[str]
    attachments: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # The sender's copy, which its outbox lists.
    outbox: Mapped[bool] = mapped_column(default=True)


class MessageRecipientRow(Base):
    __tablename__ = "message_recipient"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"), primary_key=True
    )
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # The recipient's copy; the row outlives it, since the message still names the recipient.
    inbox: Mapped[bool] = mapped_column(default=True)


class SettingRow(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class AdminActionRow(Base):
    __tablename__ = "admin_action"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The admin's subject at the sign-in provider and display name; None for the command line,
    # which knows no admin.
    admin: Mapped[str | None]
    admin_name: Mapped[str | None]
    admin_role: Mapped[str | None]
    action: Mapped[str]
    target: Mapped[str]
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ProviderRow(Base):
    __tablename__ = "provider"

    id: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    name: Mapped[str]
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GameServerRow(Base):
    __tablename__ = "game_server"

    ugid: Mapped[str] = mapped_column(primary_key=True)
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary)
    name: Mapped[str]
    state: Mapped[str]
    # No foreign key: a server keeps the id it reports also while the admin knows no such
    # provider, so that one created later is recognised.
    provider_id: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivityHourRow(Base):
    __tablename__ = "activity_hour"

    game: Mapped[str] = mapped_column(primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    online: Mapped[int]
    in_matches: Mapped[int]
    servers: Mapped[int]


class ActivityDayRow(Base):
    __tablename__ = "activity_day"

    game: Mapped[str] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    online: Mapped[int]
    in_matches: Mapped[int]
    servers: Mapped[int]


class GameIconRow(Base):
    __tablename__ = "game_icon"

    game: Mapped[str] = mapped_column(primary_key=True)
    png: Mapped[bytes] = mapped_column(LargeBinary)
