"""Login sessions (epic E7, task A2): auth_sessions, auth_refresh_tokens, canonical e-mails.

docs/SCHEMA.md sections 1 and 7. The e-mail step is one way: a downgrade keeps the canonical
(lower-case) addresses, which is harmless.

Revision ID: 008
Revises: 007
Create Date: 2026-11-01
"""

import unicodedata
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _canonical(value: str) -> str:
    # The same rule as src.core.validation.canonical_email (copied: migrations never change).
    return unicodedata.normalize("NFKC", value).strip().lower()


def _canonicalize_emails() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id, email FROM core_users WHERE email IS NOT NULL")
    ).all()
    seen: dict[str, object] = {}
    changes: list[tuple[object, str]] = []
    for user_id, email in rows:
        canonical = _canonical(email)
        if len(canonical) > 255:
            raise RuntimeError(
                f"The e-mail of user {user_id} is longer than 255 characters once normalized"
            )
        if canonical in seen:
            raise RuntimeError(
                "Two users share an e-mail address that differs only in case or spacing; "
                f"merge or correct users {seen[canonical]} and {user_id} before migrating."
            )
        seen[canonical] = user_id
        if canonical != email:
            changes.append((user_id, canonical))
    for user_id, canonical in changes:
        connection.execute(
            sa.text("UPDATE core_users SET email = :email WHERE id = :id"),
            {"email": canonical, "id": user_id},
        )


def upgrade() -> None:
    _canonicalize_emails()

    op.create_table(
        "auth_sessions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("auth_method", sa.String(16), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_kind", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "auth_method IN ('MAGIC_LINK', 'PASSWORD', 'REGISTRATION')",
            name="ck_auth_sessions_auth_method",
        ),
        sa.CheckConstraint(
            "device_kind IN ('DESKTOP_BROWSER', 'MOBILE_BROWSER', 'UNKNOWN')",
            name="ck_auth_sessions_device_kind",
        ),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "session_id",
            UUID,
            sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", UUID, sa.ForeignKey("auth_refresh_tokens.id"), nullable=True),
    )
    op.create_index("ix_auth_refresh_tokens_session_id", "auth_refresh_tokens", ["session_id"])
    op.create_index(
        "uq_auth_refresh_tokens_current",
        "auth_refresh_tokens",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("rotated_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("auth_refresh_tokens")
    op.drop_table("auth_sessions")
