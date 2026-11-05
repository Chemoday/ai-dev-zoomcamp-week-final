"""Telegram linking: one-time link codes and the per-chat attempt log (docs/SCHEMA.md 5).

Revision ID: 007
Revises: 006
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "notif_link_codes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notif_link_codes_user_id", "notif_link_codes", ["user_id"])

    op.create_table(
        "notif_link_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded", sa.Boolean, nullable=False),
    )
    op.create_index(
        "ix_notif_link_attempts_address_time",
        "notif_link_attempts",
        ["address_hash", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_table("notif_link_attempts")
    op.drop_table("notif_link_codes")
