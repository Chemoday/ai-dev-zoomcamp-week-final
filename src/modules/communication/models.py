"""Private SQLAlchemy models of the communication module. NEVER import them from another module.

Foreign keys to property_* and core_users exist in PostgreSQL, but there is no ORM relationship()
across modules: authors are shown only through PropertyService.get_masked_author_badges().
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.communication.schemas import PostCategory, PostType, TargetScope

# Keep these texts identical to alembic/versions/003_communication.py: tests compare the catalogs.
TARGET_CONSISTENT_CHECK = (
    "(target_scope = 'ALL_BUILDING' AND target_entrance IS NULL"
    " AND target_apartment_ids IS NULL)"
    " OR (target_scope = 'ENTRANCE' AND target_entrance IS NOT NULL"
    " AND target_apartment_ids IS NULL)"
    " OR (target_scope = 'APARTMENT_LIST' AND target_entrance IS NULL"
    " AND jsonb_typeof(target_apartment_ids) = 'array'"
    " AND target_apartment_ids <> '[]'::jsonb)"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(**kwargs: Any) -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        **kwargs,
    )


class Post(Base):
    """An emergency alert, an official notice or a forum topic of one building."""

    __tablename__ = "comm_posts"
    __table_args__ = (
        # Target of the comments' composite FK: a comment is in its post's building.
        UniqueConstraint("id", "building_id", name="uq_comm_posts_id_building"),
        # The author's context apartment is in the post's building (NULL = no apartment context).
        ForeignKeyConstraint(
            ["author_apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_comm_posts_author_apartment_building",
        ),
        CheckConstraint(TARGET_CONSISTENT_CHECK, name="ck_comm_posts_target_consistent"),
        CheckConstraint("target_entrance > 0", name="ck_comm_posts_target_entrance_positive"),
        CheckConstraint(
            "post_type <> 'FORUM_TOPIC' OR target_scope = 'ALL_BUILDING'",
            name="ck_comm_posts_forum_whole_building",
        ),
        # Only an emergency can be resolved (is_active = false).
        CheckConstraint(
            "post_type = 'EMERGENCY' OR is_active", name="ck_comm_posts_only_emergency_inactive"
        ),
        CheckConstraint("btrim(title) <> ''", name="ck_comm_posts_title_not_blank"),
        CheckConstraint("btrim(body) <> ''", name="ck_comm_posts_body_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False, index=True
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    author_apartment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    post_type: Mapped[PostType] = mapped_column(
        varchar_enum(PostType, "ck_comm_posts_post_type"), nullable=False, index=True
    )
    category: Mapped[PostCategory] = mapped_column(
        varchar_enum(PostCategory, "ck_comm_posts_category", length=50),
        nullable=False,
        default=PostCategory.GENERAL,
        server_default=PostCategory.GENERAL.value,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    target_scope: Mapped[TargetScope] = mapped_column(
        varchar_enum(TargetScope, "ck_comm_posts_target_scope"),
        nullable=False,
        default=TargetScope.ALL_BUILDING,
        server_default=TargetScope.ALL_BUILDING.value,
    )
    target_entrance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Apartment ids as canonical UUID strings. none_as_null: Python None is SQL NULL, not 'null'.
    target_apartment_ids: Mapped[list[str] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    comments_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = _timestamp(index=True)


class Comment(Base):
    """A comment on a post. building_id ties it, and its author's apartment, to the post's
    building."""

    __tablename__ = "comm_comments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["post_id", "building_id"],
            ["comm_posts.id", "comm_posts.building_id"],
            name="fk_comm_comments_post_building",
        ),
        ForeignKeyConstraint(
            ["author_apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_comm_comments_author_apartment_building",
        ),
        CheckConstraint("btrim(body) <> ''", name="ck_comm_comments_body_not_blank"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False
    )
    author_apartment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _timestamp()


class EmergencyAck(Base):
    """A user pressed "Acknowledge" on an emergency banner (at most once per user and post)."""

    __tablename__ = "comm_emergency_acks"
    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_comm_emergency_acks_post_user"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comm_posts.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False, index=True
    )
    acknowledged_at: Mapped[datetime] = _timestamp()
