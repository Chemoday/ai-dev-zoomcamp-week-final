"""Notifications: channel bindings, preferences, deliveries; the user's notification language.

Also moves existing reading deadlines to the end of the local day (docs/SCHEMA.md,
meters_periods.due_date): rows written by the old seed at 23:59:59 UTC become 23:59:59 in
INSTANCE_TIMEZONE on the same date.

Revision ID: 006
Revises: 005
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.core.config import settings

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

# Keep identical to src/modules/notifications/models.py: tests compare the catalogs.
CHANNELS = "('in_app', 'email', 'telegram', 'webpush')"
EVENT_TYPES = "('EMERGENCY_PUBLISHED', 'EMERGENCY_RESOLVED', 'READING_DEADLINE', 'PERIOD_REVIEW')"
LOCALES = "('et', 'ru', 'en')"

# Only rows that follow the old rule (exactly 23:59:59 UTC) are touched, so re-running is harmless.
LOCAL_DEADLINE_SQL = """
UPDATE meters_periods
SET due_date = (
    date_trunc('day', due_date AT TIME ZONE 'UTC') + interval '23:59:59'
) AT TIME ZONE :tz
WHERE (due_date AT TIME ZONE 'UTC')::time = time '23:59:59'
"""


def upgrade() -> None:
    # The same validated setting the app uses from now on (env or .env).
    op.execute(sa.text(LOCAL_DEADLINE_SQL).bindparams(tz=settings.INSTANCE_TIMEZONE))

    op.add_column("core_users", sa.Column("locale", sa.String(5), nullable=True))
    op.create_check_constraint("ck_core_users_locale", "core_users", f"locale IN {LOCALES}")

    op.create_table(
        "notif_channel_bindings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "channel IN ('telegram', 'webpush')", name="ck_notif_channel_bindings_channel"
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (config IS NOT NULL)",
            name="ck_notif_channel_bindings_config_revoked",
        ),
    )
    op.create_index("ix_notif_channel_bindings_user_id", "notif_channel_bindings", ["user_id"])
    op.create_index(
        "uq_notif_channel_bindings_active_address",
        "notif_channel_bindings",
        ["channel", "address_hash"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_notif_channel_bindings_one_telegram",
        "notif_channel_bindings",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("channel = 'telegram' AND revoked_at IS NULL"),
    )

    op.create_table(
        "notif_preferences",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "event_type", "channel", name="uq_notif_preferences_user_event_channel"
        ),
        sa.CheckConstraint(f"event_type IN {EVENT_TYPES}", name="ck_notif_preferences_event_type"),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name="ck_notif_preferences_channel"),
    )

    op.create_table(
        "notif_deliveries",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("binding_id", UUID, sa.ForeignKey("notif_channel_bindings.id"), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("dedup_key", sa.String(128), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", UUID, nullable=False),
        sa.Column(
            "params", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("locale", sa.String(5), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "dedup_key",
            "user_id",
            "channel",
            "binding_id",
            name="uq_notif_deliveries_dedup",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint(f"channel IN {CHANNELS}", name="ck_notif_deliveries_channel"),
        sa.CheckConstraint(f"event_type IN {EVENT_TYPES}", name="ck_notif_deliveries_event_type"),
        sa.CheckConstraint(
            "subject_type IN ('post', 'period')", name="ck_notif_deliveries_subject_type"
        ),
        sa.CheckConstraint(f"locale IN {LOCALES}", name="ck_notif_deliveries_locale"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'SENT', 'FAILED', 'SKIPPED')",
            name="ck_notif_deliveries_status",
        ),
        sa.CheckConstraint(
            "(binding_id IS NOT NULL) = (channel IN ('telegram', 'webpush'))",
            name="ck_notif_deliveries_binding_channel",
        ),
        sa.CheckConstraint(
            "(lease_until IS NOT NULL) = (status = 'SENDING')", name="ck_notif_deliveries_lease"
        ),
        sa.CheckConstraint(
            "read_at IS NULL OR channel = 'in_app'", name="ck_notif_deliveries_read_in_app"
        ),
        sa.CheckConstraint("attempts BETWEEN 0 AND 5", name="ck_notif_deliveries_attempts"),
        sa.CheckConstraint("priority IN (0, 1)", name="ck_notif_deliveries_priority"),
    )
    op.create_index(
        "ix_notif_deliveries_due",
        "notif_deliveries",
        ["priority", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'SENDING')"),
    )
    op.create_index(
        "ix_notif_deliveries_feed",
        "notif_deliveries",
        ["user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("channel = 'in_app'"),
    )


def downgrade() -> None:
    # Deadlines stay at local midnight: the old value is still the same day, just stricter.
    op.drop_table("notif_deliveries")
    op.drop_table("notif_preferences")
    op.drop_table("notif_channel_bindings")
    op.drop_constraint("ck_core_users_locale", "core_users", type_="check")
    op.drop_column("core_users", "locale")
