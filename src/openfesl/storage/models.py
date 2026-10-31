from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    Sequence,
    SmallInteger,
    UniqueConstraint,
    func,
    text,
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


class SignInProofRow(Base):
    __tablename__ = "sign_in_proof"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    service: Mapped[str]
    subject: Mapped[str]
    name: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PasswordResetRow(Base):
    __tablename__ = "password_reset"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    # One link per account: a new one replaces the last.
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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


class HeroAccountRow(Base):
    __tablename__ = "hero_account"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    game: Mapped[str] = mapped_column(primary_key=True)
    # The hero the game is started as; the account's first hero until another is chosen.
    default_persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("persona.id", ondelete="SET NULL")
    )
    # The slots bought on top of the game's free ones, which stay with the account.
    bought_slots: Mapped[int] = mapped_column(server_default="0")
    # The slots the admin granted on top of those, free, which stay with the account too.
    granted_slots: Mapped[int] = mapped_column(server_default="0")


class MugshotRow(Base):
    __tablename__ = "mugshot"

    # The picture of a hero that its game rendered last, which goes with the hero.
    persona_id: Mapped[int] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), primary_key=True
    )
    # Raised with each new picture, so that a copy can tell it is out of date.
    version: Mapped[int]
    image: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    # A credit's sender gives it an id so that it is credited once, however often it is sent.
    __table_args__ = (
        Index(
            "wallet_entry_transaction_key",
            "transaction",
            unique=True,
            postgresql_where=text('"transaction" IS NOT NULL'),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id", ondelete="CASCADE"), index=True)
    delta: Mapped[int] = mapped_column(BigInteger)
    # The wallet's balance after the change.
    balance: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    transaction: Mapped[str | None]


# Numbers the products and bundles of the stores together, so that an id names one thing to buy.
STORE_ID = Sequence("store_id")


class StoreProductRow(Base):
    __tablename__ = "store_product"
    # One product of each way an item is owned, priced in each currency.
    __table_args__ = (
        UniqueConstraint(
            "game",
            "item_id",
            "type",
            "days",
            "uses",
            "currency",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, STORE_ID, primary_key=True)
    game: Mapped[str]
    item_id: Mapped[int]
    type: Mapped[str]
    days: Mapped[int | None]
    uses: Mapped[int | None]
    currency: Mapped[str]
    price: Mapped[int] = mapped_column(BigInteger)
    original_price: Mapped[int | None] = mapped_column(BigInteger)
    # Owned by the account rather than one of its heroes.
    account: Mapped[bool] = mapped_column(server_default="false")
    # Unpublished products are never listed nor bought alone, only as parts of others.
    published: Mapped[bool] = mapped_column(server_default="true")
    sort: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StoreBundleRow(Base):
    __tablename__ = "store_bundle"
    # One bundle of each pick of a section's period, however many servers make it at once.
    __table_args__ = (UniqueConstraint("game", "section", "pool", "starts_at"),)

    id: Mapped[int] = mapped_column(BigInteger, STORE_ID, primary_key=True)
    game: Mapped[str]
    # The ids of texts of the game's language table, or the texts themselves.
    name: Mapped[str]
    description: Mapped[str | None]
    currency: Mapped[str]
    price: Mapped[int] = mapped_column(BigInteger)
    original_price: Mapped[int | None] = mapped_column(BigInteger)
    # The faction and class whose heroes may buy it; any where none.
    faction: Mapped[str | None]
    hero_class: Mapped[str | None]
    game_order: Mapped[int] = mapped_column(server_default="0")
    # The share off its products' prices that the admin priced it by, in percent.
    discount: Mapped[int | None] = mapped_column(SmallInteger)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published: Mapped[bool] = mapped_column(server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The box of which it gives one product at random, for a box sold as a hot deal.
    box_id: Mapped[int | None] = mapped_column(
        ForeignKey("store_box.id", ondelete="CASCADE"), index=True
    )
    # The hot deals' section it is listed in, and what the rotation picked it from.
    section: Mapped[str | None]
    pool: Mapped[str | None]
    # admin or auto, for the rotation's own.
    source: Mapped[str] = mapped_column(server_default="admin")
    # The admin's own name and description by language other than English, as two-item lists.
    texts: Mapped[dict[str, list[str | None]]] = mapped_column(JSONB, server_default="{}")
    # How many are sold in all, without a limit where none, and how many were sold.
    stock: Mapped[int | None]
    sold: Mapped[int] = mapped_column(server_default="0")


class StoreBundleMemberRow(Base):
    __tablename__ = "store_bundle_member"

    bundle_id: Mapped[int] = mapped_column(
        ForeignKey("store_bundle.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("store_product.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class StoreBoxRow(Base):
    __tablename__ = "store_box"
    __table_args__ = (UniqueConstraint("game", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    game: Mapped[str]
    # The name the game's data knows it by.
    key: Mapped[str]
    kind: Mapped[str]
    # What every product it gives is worth at least, in its currency, while it promises so.
    currency: Mapped[str]
    value: Mapped[int] = mapped_column(BigInteger)
    worth_rule: Mapped[bool] = mapped_column(server_default="true")
    # Whether it is sold, or for a chest whether keys open it.
    enabled: Mapped[bool] = mapped_column(server_default="true")
    # The faction and class whose heroes may open it; any where none.
    faction: Mapped[str | None]
    hero_class: Mapped[str | None]
    # What it gives a hero who may get none of its drops.
    fallback_id: Mapped[int | None] = mapped_column(
        ForeignKey("store_product.id", ondelete="SET NULL")
    )


class StoreDropRow(Base):
    __tablename__ = "store_drop"

    box_id: Mapped[int] = mapped_column(
        ForeignKey("store_box.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("store_product.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # A tier's drops are equally likely, the tiers as likely as their weights.
    tier: Mapped[int]
    weight: Mapped[int]


class DealArtRow(Base):
    __tablename__ = "deal_art"

    # A picture of a hot deal's offer, by the offer's name, that the game shows in place of the
    # generated one: big or small.
    game: Mapped[str] = mapped_column(primary_key=True)
    offer: Mapped[str] = mapped_column(primary_key=True)
    big: Mapped[bool] = mapped_column(primary_key=True)
    image: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseRow(Base):
    __tablename__ = "purchase"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    game: Mapped[str]
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    # The hero who bought, for what a hero owns.
    persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("persona.id", ondelete="CASCADE"), index=True
    )
    # The id the game bought it by, and whether it was a product, a bundle or a box.
    product_id: Mapped[str]
    kind: Mapped[str]
    currency: Mapped[str]
    price: Mapped[int] = mapped_column(BigInteger)
    # What the hero or account gained.
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RewardClaimRow(Base):
    __tablename__ = "reward_claim"
    # Each reward once for each account, and each of its factions' parts once; the account's own
    # part has no faction.
    __table_args__ = (
        UniqueConstraint(
            "game", "account_id", "key", "faction", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    game: Mapped[str]
    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str]
    faction: Mapped[str | None]
    # The hero its items went to.
    persona_id: Mapped[int | None] = mapped_column(
        ForeignKey("persona.id", ondelete="SET NULL"), index=True
    )
    # What the account and the hero got.
    outcome: Mapped[dict[str, Any]] = mapped_column(JSONB)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RewardBaselineRow(Base):
    __tablename__ = "reward_baseline"

    game: Mapped[str] = mapped_column(primary_key=True)
    # The event of a year whose goals count from it, and the stat it holds.
    key: Mapped[str] = mapped_column(primary_key=True)
    stat: Mapped[str] = mapped_column(primary_key=True)
    # Every hero's value of the stat when it was taken, by persona.
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RewardPauseRow(Base):
    __tablename__ = "reward_pause"

    game: Mapped[str] = mapped_column(primary_key=True)
    # A day on which claims were paused, whose daily gifts wait until their events end.
    day: Mapped[date] = mapped_column(primary_key=True)


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
    # When it can no longer be redeemed; never when None.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What a code of a game's site gives, as the rewards write a gift; None for a key that grants
    # entitlement tags.
    gift: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The first key of the batch it was made in, one account each; None for a key of its own.
    batch: Mapped[int | None] = mapped_column(BigInteger, index=True)


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
    # The hero a code's items went to.
    persona_id: Mapped[int | None] = mapped_column(ForeignKey("persona.id", ondelete="SET NULL"))
    # What a code gave the account and the hero; None for a key that grants entitlement tags.
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


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


class RelationshipRow(Base):
    __tablename__ = "relationship"
    # One side of a relationship of an account with an account or a dedicated server; the other
    # side is a row of its own. Each side is either an account or a server, by its name.
    __table_args__ = (
        UniqueConstraint(
            "game",
            "tag",
            "owner_id",
            "owner_server",
            "member_id",
            "member_server",
            name="relationship_side_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(owner_id IS NULL) <> (owner_server IS NULL)", name="relationship_one_owner"
        ),
        CheckConstraint(
            "(member_id IS NULL) <> (member_server IS NULL)", name="relationship_one_member"
        ),
    )

    # The number the owner names its side by.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    game: Mapped[str]
    tag: Mapped[str]
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    owner_server: Mapped[str | None]
    member_id: Mapped[int | None] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    member_server: Mapped[str | None]
    # Whether each side asked for the relationship or accepted it; both sides' rows say it.
    owner_agrees: Mapped[bool]
    member_agrees: Mapped[bool]
    # Whether the member changed the relationship since the owner last acknowledged it.
    new: Mapped[bool]
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
    official: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CatalogueRow(Base):
    __tablename__ = "catalogue"

    partition: Mapped[str] = mapped_column(primary_key=True)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The import the document comes from, whose folder holds its files.
    revision: Mapped[str]
    icons: Mapped[int]
    models: Mapped[int] = mapped_column(server_default="0")
    textures: Mapped[int] = mapped_column(server_default="0")
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CatalogueTextRow(Base):
    __tablename__ = "catalogue_text"

    partition: Mapped[str] = mapped_column(
        ForeignKey("catalogue.partition", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(primary_key=True)
    texts: Mapped[dict[str, str]] = mapped_column(JSONB)


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
