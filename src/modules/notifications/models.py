"""Private SQLAlchemy models of the notifications module. NEVER import them from another module.

docs/SCHEMA.md section 5. The link-code tables of Telegram come with the Telegram channel (N4).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.notifications.schemas import (
    Channel,
    DeliveryStatus,
    NotificationEventType,
    SubjectType,
)
from src.modules.property.schemas import Locale


def _now() -> datetime:
    return datetime.now(UTC)


class ChannelBinding(Base):
    """Where a user can be reached on an external channel: one Telegram chat or one device."""

    __tablename__ = "notif_channel_bindings"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('telegram', 'webpush')", name="ck_notif_channel_bindings_channel"
        ),
        CheckConstraint(
            "(revoked_at IS NULL) = (config IS NOT NULL)",
            name="ck_notif_channel_bindings_config_revoked",
        ),
        Index(
            "uq_notif_channel_bindings_active_address",
            "channel",
            "address_hash",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "uq_notif_channel_bindings_one_telegram",
            "user_id",
            unique=True,
            postgresql_where=text("channel = 'telegram' AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False, index=True
    )
    # Only the external channels with an address have bindings (e-mail uses core_users.email).
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Preference(Base):
    """A deviation from the default preference of one event type on one channel."""

    __tablename__ = "notif_preferences"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "event_type", "channel", name="uq_notif_preferences_user_event_channel"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    event_type: Mapped[NotificationEventType] = mapped_column(
        varchar_enum(NotificationEventType, "ck_notif_preferences_event_type"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(
        varchar_enum(Channel, "ck_notif_preferences_channel", length=16), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class Delivery(Base):
    """One notification to one user on one channel (and one device). No text, no contact data."""

    __tablename__ = "notif_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "dedup_key",
            "user_id",
            "channel",
            "binding_id",
            name="uq_notif_deliveries_dedup",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(binding_id IS NOT NULL) = (channel IN ('telegram', 'webpush'))",
            name="ck_notif_deliveries_binding_channel",
        ),
        CheckConstraint(
            "(lease_until IS NOT NULL) = (status = 'SENDING')", name="ck_notif_deliveries_lease"
        ),
        CheckConstraint(
            "read_at IS NULL OR channel = 'in_app'", name="ck_notif_deliveries_read_in_app"
        ),
        CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_notif_deliveries_attempts"),
        CheckConstraint("priority IN (0, 1)", name="ck_notif_deliveries_priority"),
        Index(
            "ix_notif_deliveries_due",
            "priority",
            "next_attempt_at",
            postgresql_where=text("status IN ('PENDING', 'SENDING')"),
        ),
        Index(
            "ix_notif_deliveries_feed",
            "user_id",
            text("created_at DESC"),
            postgresql_where=text("channel = 'in_app'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(
        varchar_enum(Channel, "ck_notif_deliveries_channel", length=16), nullable=False
    )
    binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("notif_channel_bindings.id"), nullable=True
    )
    event_type: Mapped[NotificationEventType] = mapped_column(
        varchar_enum(NotificationEventType, "ck_notif_deliveries_event_type"), nullable=False
    )
    dedup_key: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_type: Mapped[SubjectType] = mapped_column(
        varchar_enum(SubjectType, "ck_notif_deliveries_subject_type", length=16), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    locale: Mapped[Locale] = mapped_column(
        varchar_enum(Locale, "ck_notif_deliveries_locale", length=5), nullable=False
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(
        varchar_enum(DeliveryStatus, "ck_notif_deliveries_status", length=16), nullable=False
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
