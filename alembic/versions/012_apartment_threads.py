"""Board <-> apartment threads (epic E8, task P2).

docs/SCHEMA.md section 4: post type APARTMENT_THREAD (one apartment, always open for replies)
and comm_posts.last_activity_at, filled from the newest comment of every existing post.

Revision ID: 012
Revises: 011
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep these texts identical to src/modules/communication/models.py: tests compare the catalogs.
THREAD_ONE_APARTMENT_CHECK = (
    "post_type <> 'APARTMENT_THREAD' OR (target_scope = 'APARTMENT_LIST'"
    " AND NOT jsonb_path_exists(target_apartment_ids, '$[1]'))"
)
POST_TYPES_BEFORE = "('EMERGENCY', 'OFFICIAL_NOTICE', 'FORUM_TOPIC')"
POST_TYPES_AFTER = "('EMERGENCY', 'OFFICIAL_NOTICE', 'FORUM_TOPIC', 'APARTMENT_THREAD')"


def upgrade() -> None:
    op.drop_constraint("ck_comm_posts_post_type", "comm_posts", type_="check")
    op.create_check_constraint(
        "ck_comm_posts_post_type", "comm_posts", f"post_type IN {POST_TYPES_AFTER}"
    )

    op.add_column(
        "comm_posts",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        "UPDATE comm_posts SET last_activity_at = greatest(created_at, "
        "(SELECT max(c.created_at) FROM comm_comments c WHERE c.post_id = comm_posts.id))"
    )
    op.alter_column("comm_posts", "last_activity_at", nullable=False)

    op.create_check_constraint(
        "ck_comm_posts_thread_one_apartment", "comm_posts", THREAD_ONE_APARTMENT_CHECK
    )
    op.create_check_constraint(
        "ck_comm_posts_thread_comments_enabled",
        "comm_posts",
        "post_type <> 'APARTMENT_THREAD' OR comments_enabled",
    )
    op.create_check_constraint(
        "ck_comm_posts_activity_after_creation", "comm_posts", "last_activity_at >= created_at"
    )
    op.create_index(
        "ix_comm_posts_building_type_activity",
        "comm_posts",
        ["building_id", "post_type", "last_activity_at"],
    )


def downgrade() -> None:
    # The old post types cannot hold threads: they are removed with their replies.
    op.execute(
        "DELETE FROM comm_comments WHERE post_id IN "
        "(SELECT id FROM comm_posts WHERE post_type = 'APARTMENT_THREAD')"
    )
    op.execute("DELETE FROM comm_posts WHERE post_type = 'APARTMENT_THREAD'")

    op.drop_index("ix_comm_posts_building_type_activity", table_name="comm_posts")
    op.drop_constraint("ck_comm_posts_activity_after_creation", "comm_posts", type_="check")
    op.drop_constraint("ck_comm_posts_thread_comments_enabled", "comm_posts", type_="check")
    op.drop_constraint("ck_comm_posts_thread_one_apartment", "comm_posts", type_="check")
    op.drop_column("comm_posts", "last_activity_at")

    op.drop_constraint("ck_comm_posts_post_type", "comm_posts", type_="check")
    op.create_check_constraint(
        "ck_comm_posts_post_type", "comm_posts", f"post_type IN {POST_TYPES_BEFORE}"
    )
