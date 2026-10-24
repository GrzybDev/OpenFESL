from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, LargeBinary, func
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


Index("account_nuid_key", func.lower(AccountRow.nuid), unique=True)


class LoginTokenRow(Base):
    __tablename__ = "login_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
