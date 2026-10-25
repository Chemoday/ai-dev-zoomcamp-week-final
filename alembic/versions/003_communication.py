"""Communication: posts (emergencies, official notices, forum topics), comments, emergency acks.

Revision ID: 003
Revises: 002
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)

# Keep identical to src/modules/communication/models.py: tests compare the catalogs.
TARGET_CONSISTENT_CHECK = (
    "(target_scope = 'ALL_BUILDING' AND target_entrance IS NULL"
    " AND target_apartment_ids IS NULL)"
    " OR (target_scope = 'ENTRANCE' AND target_entrance IS NOT NULL"
    " AND target_apartment_ids IS NULL)"
    " OR (target_scope = 'APARTMENT_LIST' AND target_entrance IS NULL"
    " AND jsonb_typeof(target_apartment_ids) = 'array'"
    " AND target_apartment_ids <> '[]'::jsonb)"
)


def _created_at(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "comm_posts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("building_id", UUID, sa.ForeignKey("property_buildings.id"), nullable=False),
        sa.Column("author_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("author_apartment_id", UUID, nullable=True),
        sa.Column("post_type", sa.String(32), nullable=False),
        sa.Column("category", sa.String(50), nullable=False, server_default="GENERAL"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("target_scope", sa.String(32), nullable=False, server_default="ALL_BUILDING"),
        sa.Column("target_entrance", sa.Integer(), nullable=True),
        sa.Column("target_apartment_ids", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("comments_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at("created_at"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.UniqueConstraint("id", "building_id", name="uq_comm_posts_id_building"),
        sa.ForeignKeyConstraint(
            ["author_apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_comm_posts_author_apartment_building",
        ),
        sa.CheckConstraint(
            "post_type IN ('EMERGENCY', 'OFFICIAL_NOTICE', 'FORUM_TOPIC')",
            name="ck_comm_posts_post_type",
        ),
        sa.CheckConstraint(
            "category IN ('GENERAL', 'WATER_SHUTOFF', 'MAINTENANCE', 'MARKETPLACE')",
            name="ck_comm_posts_category",
        ),
        sa.CheckConstraint(
            "target_scope IN ('ALL_BUILDING', 'ENTRANCE', 'APARTMENT_LIST')",
            name="ck_comm_posts_target_scope",
        ),
        sa.CheckConstraint(TARGET_CONSISTENT_CHECK, name="ck_comm_posts_target_consistent"),
        sa.CheckConstraint("target_entrance > 0", name="ck_comm_posts_target_entrance_positive"),
        sa.CheckConstraint(
            "post_type <> 'FORUM_TOPIC' OR target_scope = 'ALL_BUILDING'",
            name="ck_comm_posts_forum_whole_building",
        ),
        sa.CheckConstraint(
            "post_type = 'EMERGENCY' OR is_active", name="ck_comm_posts_only_emergency_inactive"
        ),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_comm_posts_title_not_blank"),
        sa.CheckConstraint("btrim(body) <> ''", name="ck_comm_posts_body_not_blank"),
        sa.CheckConstraint(
            "post_type = 'FORUM_TOPIC' OR deleted_at IS NULL",
            name="ck_comm_posts_only_forum_deleted",
        ),
        sa.CheckConstraint(
            "(deleted_at IS NULL) = (deleted_by_user_id IS NULL)",
            name="ck_comm_posts_deleted_consistent",
        ),
    )
    op.create_index("ix_comm_posts_building_id", "comm_posts", ["building_id"])
    op.create_index("ix_comm_posts_post_type", "comm_posts", ["post_type"])
    op.create_index("ix_comm_posts_created_at", "comm_posts", ["created_at"])

    op.create_table(
        "comm_comments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("post_id", UUID, nullable=False),
        sa.Column("building_id", UUID, sa.ForeignKey("property_buildings.id"), nullable=False),
        sa.Column("author_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("author_apartment_id", UUID, nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        _created_at("created_at"),
        sa.ForeignKeyConstraint(
            ["post_id", "building_id"],
            ["comm_posts.id", "comm_posts.building_id"],
            name="fk_comm_comments_post_building",
        ),
        sa.ForeignKeyConstraint(
            ["author_apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_comm_comments_author_apartment_building",
        ),
        sa.CheckConstraint("btrim(body) <> ''", name="ck_comm_comments_body_not_blank"),
    )
    op.create_index("ix_comm_comments_post_id", "comm_comments", ["post_id"])

    op.create_table(
        "comm_emergency_acks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("post_id", UUID, sa.ForeignKey("comm_posts.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("core_users.id"), nullable=False),
        _created_at("acknowledged_at"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_comm_emergency_acks_post_user"),
    )
    op.create_index("ix_comm_emergency_acks_user_id", "comm_emergency_acks", ["user_id"])


def downgrade() -> None:
    op.drop_table("comm_emergency_acks")
    op.drop_table("comm_comments")
    op.drop_table("comm_posts")
