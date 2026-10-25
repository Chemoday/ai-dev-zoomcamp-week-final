"""Core users and identities, property buildings, apartments and memberships.

Revision ID: 001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "property_buildings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(500), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False, server_default="EE"),
        sa.Column(
            "reading_deadline_day", sa.Integer(), nullable=False, server_default=sa.text("4")
        ),
    )

    op.create_table(
        "core_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "building_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("property_buildings.id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("building_role", sa.String(32), nullable=False, server_default="USER"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("email", name="core_users_email_key"),
        sa.CheckConstraint(
            "building_role IN ('CHAIRMAN', 'BOARD', 'ACCOUNTANT', 'USER')",
            name="ck_core_users_building_role",
        ),
    )
    op.create_index("ix_core_users_building_id", "core_users", ["building_id"])

    op.create_table(
        "core_user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core_users.id"), nullable=False
        ),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("subject_hash", sa.String(128), nullable=False),
        sa.Column("secret_data", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "provider", "subject_hash", name="uq_core_user_identities_provider_subject"
        ),
    )
    op.create_index("ix_core_user_identities_user_id", "core_user_identities", ["user_id"])
    op.create_index(
        "ix_core_user_identities_subject_hash", "core_user_identities", ["subject_hash"]
    )

    op.create_table(
        "property_apartments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "building_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("property_buildings.id"),
            nullable=False,
        ),
        sa.Column("number", sa.String(32), nullable=False),
        sa.Column("entrance", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("floor", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("riser_group", sa.String(50), nullable=True),
        sa.Column("area_m2", sa.Numeric(8, 2), nullable=False),
        sa.UniqueConstraint("building_id", "number", name="uq_property_apartments_building_number"),
    )

    op.create_table(
        "property_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "apartment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("property_apartments.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core_users.id"), nullable=False
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column(
            "can_submit_readings", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("can_view_bills", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("can_vote", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "privacy_display_mode",
            sa.String(32),
            nullable=False,
            server_default="APARTMENT_ONLY",
        ),
        sa.UniqueConstraint(
            "apartment_id", "user_id", name="uq_property_memberships_apartment_user"
        ),
        sa.CheckConstraint(
            "relation_type IN ('OWNER', 'TENANT', 'RESIDENT')",
            name="ck_property_memberships_relation_type",
        ),
        sa.CheckConstraint(
            "privacy_display_mode IN ('APARTMENT_ONLY', 'INITIALS', 'FULL_NAME')",
            name="ck_property_memberships_privacy_display_mode",
        ),
    )
    op.create_index(
        "ix_property_memberships_apartment_id", "property_memberships", ["apartment_id"]
    )
    op.create_index("ix_property_memberships_user_id", "property_memberships", ["user_id"])


def downgrade() -> None:
    op.drop_table("property_memberships")
    op.drop_table("property_apartments")
    op.drop_table("core_user_identities")
    op.drop_table("core_users")
    op.drop_table("property_buildings")
