"""The password of module auth (epic E7, task A5).

docs/SCHEMA.md sections 1 and 7: core_user_identities now belongs to module auth. It gets
created_at, the flag requires_link_sign_in (set after 100 failed logins in a day) and a partial
unique index, so an account has at most one password.

Revision ID: 011
Revises: 010
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "core_user_identities",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "core_user_identities",
        sa.Column(
            "requires_link_sign_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "uq_core_user_identities_password_user",
        "core_user_identities",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("provider = 'password'"),
    )


def downgrade() -> None:
    op.drop_index("uq_core_user_identities_password_user", table_name="core_user_identities")
    op.drop_column("core_user_identities", "requires_link_sign_in")
    op.drop_column("core_user_identities", "created_at")
