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
    # Set while the account waits for the link that confirms its e-mail address.
    verification_hash: Mapped[bytes | None] = mapped_column(LargeBinary, unique=True)
    verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("account_nuid_key", func.lower(AccountRow.nuid), unique=True)


class LoginTokenRow(Base):
    __tablename__ = "login_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    # One row per tag, so a key entered again grants nothing twice and a revoked tag stays so.
    __table_args__ = (UniqueConstraint("account_id", "tag"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    tag: Mapped[str]
    group_name: Mapped[str | None]
    product_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(default="ACTIVE")
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductKeyRow(Base):
    __tablename__ = "product_key"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # How many accounts may redeem it; None for any number.
    max_uses: Mapped[int | None]
    once_per_account: Mapped[bool] = mapped_column(server_default="false")


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
    # None for the command line, which knows no admin.
    admin: Mapped[str | None]
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
