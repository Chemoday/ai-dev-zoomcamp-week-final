"""Private SQLAlchemy models of the property module. NEVER import them from another module.

Users live here (tables keep their core_ prefix, see docs/SCHEMA.md); other modules read users
only through DTOs from core.security or PropertyService.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType


class Building(Base):
    __tablename__ = "property_buildings"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    country_code: Mapped[str] = mapped_column(
        String(2), nullable=False, default="EE", server_default="EE"
    )
    reading_deadline_day: Mapped[int] = mapped_column(
        Integer, nullable=False, default=4, server_default=text("4")
    )


class User(Base):
    __tablename__ = "core_users"

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False, index=True
    )
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    building_role: Mapped[BuildingRole] = mapped_column(
        varchar_enum(BuildingRole, "ck_core_users_building_role"),
        nullable=False,
        default=BuildingRole.USER,
        server_default=BuildingRole.USER.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )


class UserIdentity(Base):
    __tablename__ = "core_user_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider", "subject_hash", name="uq_core_user_identities_provider_subject"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    subject_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    secret_data: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Apartment(Base):
    __tablename__ = "property_apartments"
    __table_args__ = (
        UniqueConstraint("building_id", "number", name="uq_property_apartments_building_number"),
        # Lets other tables prove an apartment is in their building with a composite FK.
        UniqueConstraint("id", "building_id", name="uq_property_apartments_id_building"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    entrance: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    floor: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    riser_group: Mapped[str | None] = mapped_column(String(50), nullable=True)
    area_m2: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)


class ApartmentMembership(Base):
    __tablename__ = "property_memberships"
    __table_args__ = (
        UniqueConstraint("apartment_id", "user_id", name="uq_property_memberships_apartment_user"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    apartment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_apartments.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=False, index=True
    )
    relation_type: Mapped[RelationType] = mapped_column(
        varchar_enum(RelationType, "ck_property_memberships_relation_type"), nullable=False
    )
    can_submit_readings: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    can_view_bills: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    can_vote: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    privacy_display_mode: Mapped[DisplayMode] = mapped_column(
        varchar_enum(DisplayMode, "ck_property_memberships_privacy_display_mode"),
        nullable=False,
        default=DisplayMode.APARTMENT_ONLY,
        server_default=DisplayMode.APARTMENT_ONLY.value,
    )
