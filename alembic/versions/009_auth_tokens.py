"""One-time sign-in links and the attempt log of module auth (epic E7, task A3).

docs/SCHEMA.md section 7. auth_tokens gets its invitation columns with A4.

Revision ID: 009
Revises: 008
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "auth_tokens",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('MAGIC_LINK', 'INVITATION', 'REGISTRATION')",
            name="ck_auth_tokens_purpose",
        ),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])
    op.create_index("ix_auth_tokens_expires_at", "auth_tokens", ["expires_at"])

    op.create_table(
        "auth_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("subject_hash", sa.String(64), nullable=False),
        sa.Column("succeeded", sa.Boolean, nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('MAGIC_LINK', 'REDEEM', 'PASSWORD', 'INVITATION')",
            name="ck_auth_attempts_kind",
        ),
    )
    op.create_index(
        "ix_auth_attempts_kind_subject_time",
        "auth_attempts",
        ["kind", "subject_hash", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_table("auth_attempts")
    op.drop_table("auth_tokens")
