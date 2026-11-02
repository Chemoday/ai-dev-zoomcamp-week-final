"""Invitations of module auth, and when memberships start and end (epic E7, task A4).

docs/SCHEMA.md sections 2 and 7: property_memberships.started_at / ended_at, auth_invitations, and
the invitation columns of auth_tokens. Existing memberships get the time of the migration as
their start.

Revision ID: 010
Revises: 009
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "property_memberships",
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "property_memberships", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "auth_invitations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("building_id", UUID, sa.ForeignKey("property_buildings.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("apartment_id", UUID, sa.ForeignKey("property_apartments.id"), nullable=True),
        sa.Column("building_role", sa.String(32), nullable=True),
        sa.Column("invited_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.Column("membership_id", UUID, sa.ForeignKey("property_memberships.id"), nullable=True),
        sa.Column("applied", sa.Boolean, nullable=True),
        sa.CheckConstraint("kind IN ('OWNER', 'TENANT', 'STAFF')", name="ck_auth_invitations_kind"),
        sa.CheckConstraint(
            "building_role IN ('BOARD', 'ACCOUNTANT', 'CHAIRMAN')",
            name="ck_auth_invitations_building_role",
        ),
        sa.CheckConstraint(
            "(apartment_id IS NOT NULL) = (kind IN ('OWNER', 'TENANT')) "
            "AND (building_role IS NOT NULL) = (kind = 'STAFF')",
            name="ck_auth_invitations_target",
        ),
    )
    op.create_index("ix_auth_invitations_email", "auth_invitations", ["email"])
    op.create_index("ix_auth_invitations_building_id", "auth_invitations", ["building_id"])
    op.create_index(
        "uq_auth_invitations_open",
        "auth_invitations",
        ["kind", "email", "apartment_id", "building_role"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_auth_invitations_open_tenants",
        "auth_invitations",
        ["apartment_id"],
        postgresql_where=sa.text("kind = 'TENANT' AND accepted_at IS NULL"),
    )

    op.add_column(
        "auth_tokens",
        sa.Column(
            "invitation_id",
            UUID,
            sa.ForeignKey("auth_invitations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_auth_tokens_invitation_id", "auth_tokens", ["invitation_id"])
    op.create_index(
        "uq_auth_tokens_invitation_link",
        "auth_tokens",
        ["invitation_id"],
        unique=True,
        postgresql_where=sa.text("purpose = 'INVITATION'"),
    )
    op.create_check_constraint(
        "ck_auth_tokens_owner",
        "auth_tokens",
        "(purpose = 'MAGIC_LINK' AND user_id IS NOT NULL AND invitation_id IS NULL) "
        "OR (purpose <> 'MAGIC_LINK' AND user_id IS NULL AND invitation_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_auth_tokens_owner", "auth_tokens", type_="check")
    op.drop_index("uq_auth_tokens_invitation_link", table_name="auth_tokens")
    op.drop_index("ix_auth_tokens_invitation_id", table_name="auth_tokens")
    op.drop_column("auth_tokens", "invitation_id")
    op.drop_table("auth_invitations")
    op.drop_column("property_memberships", "ended_at")
    op.drop_column("property_memberships", "started_at")
