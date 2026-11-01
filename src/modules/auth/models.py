"""Private SQLAlchemy models of the auth module. NEVER import them from another module.

docs/SCHEMA.md section 7.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.auth.schemas import AuthMethod, DeviceKind


class AuthSession(Base):
    """One sign-in on one device. Its refresh tokens rotate; `id` is the `sid` claim."""

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False, index=True
    )
    auth_method: Mapped[AuthMethod] = mapped_column(
        varchar_enum(AuthMethod, "ck_auth_sessions_auth_method", length=16), nullable=False
    )
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_kind: Mapped[DeviceKind] = mapped_column(
        varchar_enum(DeviceKind, "ck_auth_sessions_device_kind", length=16), nullable=False
    )


class RefreshToken(Base):
    """One refresh cookie value, stored only as its keyed hash."""

    __tablename__ = "auth_refresh_tokens"
    __table_args__ = (
        Index(
            "uq_auth_refresh_tokens_current",
            "session_id",
            unique=True,
            postgresql_where=text("rotated_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_refresh_tokens.id"), nullable=True
    )
