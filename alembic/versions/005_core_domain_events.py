"""Core: durable domain events (core_domain_events).

Revision ID: 005
Revises: 004
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_domain_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("building_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("traceparent", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_core_domain_events_unpublished",
        "core_domain_events",
        ["next_attempt_at"],
        postgresql_where=sa.text("published_at IS NULL AND failed_at IS NULL"),
    )
    op.create_index(
        "ix_core_domain_events_building_time",
        "core_domain_events",
        ["building_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_core_domain_events_building_time", table_name="core_domain_events")
    op.drop_index("ix_core_domain_events_unpublished", table_name="core_domain_events")
    op.drop_table("core_domain_events")
