"""The board's micro-forum (epic E8, task P4).

docs/SCHEMA.md section 4: post type BOARD_TOPIC (whole building, no author apartment), and the
deletion check now admits board topics next to forum topics.

Revision ID: 014
Revises: 013
Create Date: 2026-11-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep these texts identical to src/modules/communication/models.py: tests compare the catalogs.
POST_TYPES_BEFORE = "('EMERGENCY', 'OFFICIAL_NOTICE', 'FORUM_TOPIC', 'APARTMENT_THREAD')"
POST_TYPES_AFTER = (
    "('EMERGENCY', 'OFFICIAL_NOTICE', 'FORUM_TOPIC', 'APARTMENT_THREAD', 'BOARD_TOPIC')"
)
BOARD_TOPIC_CHECK = (
    "post_type <> 'BOARD_TOPIC' OR (target_scope = 'ALL_BUILDING' AND author_apartment_id IS NULL)"
)


def _post_types(values: str) -> None:
    op.drop_constraint("ck_comm_posts_post_type", "comm_posts", type_="check")
    op.create_check_constraint("ck_comm_posts_post_type", "comm_posts", f"post_type IN {values}")


def upgrade() -> None:
    _post_types(POST_TYPES_AFTER)
    op.drop_constraint("ck_comm_posts_only_forum_deleted", "comm_posts", type_="check")
    op.create_check_constraint(
        "ck_comm_posts_only_topics_deleted",
        "comm_posts",
        "post_type IN ('FORUM_TOPIC', 'BOARD_TOPIC') OR deleted_at IS NULL",
    )
    op.create_check_constraint(
        "ck_comm_posts_board_topic_whole_building", "comm_posts", BOARD_TOPIC_CHECK
    )


def downgrade() -> None:
    # The old post types cannot hold board topics: they go with their comments.
    op.execute(
        "DELETE FROM comm_comments WHERE post_id IN "
        "(SELECT id FROM comm_posts WHERE post_type = 'BOARD_TOPIC')"
    )
    op.execute("DELETE FROM comm_posts WHERE post_type = 'BOARD_TOPIC'")
    op.drop_constraint("ck_comm_posts_board_topic_whole_building", "comm_posts", type_="check")
    op.drop_constraint("ck_comm_posts_only_topics_deleted", "comm_posts", type_="check")
    op.create_check_constraint(
        "ck_comm_posts_only_forum_deleted",
        "comm_posts",
        "post_type = 'FORUM_TOPIC' OR deleted_at IS NULL",
    )
    _post_types(POST_TYPES_BEFORE)
