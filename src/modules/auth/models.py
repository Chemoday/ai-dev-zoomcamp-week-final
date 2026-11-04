"""Private SQLAlchemy models of the auth module. NEVER import them from another module.

docs/SCHEMA.md section 7.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.auth.schemas import (
    AttemptKind,
    AuthMethod,
    DeviceKind,
    InvitationKind,
    StaffRole,
    TokenPurpose,
)


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


class AuthInvitation(Base):
    """An invitation into an apartment or a staff role (docs/SCHEMA.md 7). No account exists until
    it is accepted."""

    __tablename__ = "auth_invitations"
    __table_args__ = (
        CheckConstraint(
            "(apartment_id IS NOT NULL) = (kind IN ('OWNER', 'TENANT')) "
            "AND (building_role IS NOT NULL) = (kind = 'STAFF')",
            name="ck_auth_invitations_target",
        ),
        # One open invitation per target, from anyone.
        Index(
            "uq_auth_invitations_open",
            "kind",
            "email",
            "apartment_id",
            "building_role",
            unique=True,
            postgresql_where=text("accepted_at IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
        Index(
            "ix_auth_invitations_open_tenants",
            "apartment_id",
            postgresql_where=text("kind = 'TENANT' AND accepted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False, index=True
    )
    kind: Mapped[InvitationKind] = mapped_column(
        varchar_enum(InvitationKind, "ck_auth_invitations_kind", length=16), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    apartment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_apartments.id"), nullable=True
    )
    building_role: Mapped[StaffRole | None] = mapped_column(
        varchar_enum(StaffRole, "ck_auth_invitations_building_role"), nullable=True
    )
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=True
    )
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_memberships.id"), nullable=True
    )
    applied: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class AuthToken(Base):
    """A one-time link: a sign-in, an invitation, or a registration after an invitation. Only its
    keyed hash is stored; the plain token exists in the e-mail and in memory while it is sent."""

    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "(purpose = 'MAGIC_LINK' AND user_id IS NOT NULL AND invitation_id IS NULL) "
            "OR (purpose <> 'MAGIC_LINK' AND user_id IS NULL AND invitation_id IS NOT NULL)",
            name="ck_auth_tokens_owner",
        ),
        # At most one invitation link per invitation (a resend replaces it).
        Index(
            "uq_auth_tokens_invitation_link",
            "invitation_id",
            unique=True,
            postgresql_where=text("purpose = 'INVITATION'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    purpose: Mapped[TokenPurpose] = mapped_column(
        varchar_enum(TokenPurpose, "ck_auth_tokens_purpose", length=16), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=True, index=True
    )
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth_invitations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthAttempt(Base):
    """One counted request for the limits (by keyed hash of an e-mail, an address or both)."""

    __tablename__ = "auth_attempts"
    __table_args__ = (
        Index("ix_auth_attempts_kind_subject_time", "kind", "subject_hash", "attempted_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[AttemptKind] = mapped_column(
        varchar_enum(AttemptKind, "ck_auth_attempts_kind", length=16), nullable=False
    )
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserIdentity(Base):
    """A way to sign in besides the e-mail link: the password (A5), later eID subjects.

    For the password, `subject_hash` is the keyed hash of "user:<id>" and `secret_data` the encoded
    Argon2id hash. The table keeps its `core_` name (docs/SCHEMA.md 1 and 7).
    """

    __tablename__ = "core_user_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider", "subject_hash", name="uq_core_user_identities_provider_subject"
        ),
        # One password per account, also after AUTH_HASH_SECRET was rotated.
        Index(
            "uq_core_user_identities_password_user",
            "user_id",
            unique=True,
            postgresql_where=text("provider = 'password'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    secret_data: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set after 100 failed logins in a day; cleared by a sign-in with an e-mail link.
    requires_link_sign_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
