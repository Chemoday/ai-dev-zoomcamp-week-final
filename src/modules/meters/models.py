"""Private SQLAlchemy models of the meters module. NEVER import them from another module.

Foreign keys to property_* and core_users tables exist in PostgreSQL, but there is no ORM
relationship() across modules: property data is fetched through PropertyService DTOs.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base, uuid_pk, varchar_enum
from src.modules.meters.schemas import MeterUnit, PeriodStatus, ReadingSource, ResourceType


def _meter_value(**kwargs) -> Mapped[Decimal]:
    return mapped_column(Numeric(12, 3), **kwargs)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(**kwargs) -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
        **kwargs,
    )


class MeterDevice(Base):
    """A physical meter. apartment_id NULL means a Building Master Meter in the basement."""

    __tablename__ = "meters_devices"
    __table_args__ = (
        # Target of the readings' composite FK: a reading's meter is in the reading's building.
        UniqueConstraint("id", "building_id", name="uq_meters_devices_id_building"),
        # An apartment meter's apartment is in the meter's building (NULL = master meter, skipped).
        ForeignKeyConstraint(
            ["apartment_id", "building_id"],
            ["property_apartments.id", "property_apartments.building_id"],
            name="fk_meters_devices_apartment_building",
        ),
        CheckConstraint("initial_value >= 0", name="ck_meters_devices_initial_value_non_negative"),
        CheckConstraint(
            "max_monthly_delta > 0", name="ck_meters_devices_max_monthly_delta_positive"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False, index=True
    )
    apartment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    resource_type: Mapped[ResourceType] = mapped_column(
        varchar_enum(ResourceType, "ck_meters_devices_resource_type"), nullable=False
    )
    serial_number: Mapped[str] = mapped_column(String(100), nullable=False)
    location_label: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Main", server_default="Main"
    )
    unit: Mapped[MeterUnit] = mapped_column(
        varchar_enum(MeterUnit, "ck_meters_devices_unit", length=16),
        nullable=False,
        default=MeterUnit.M3,
        server_default=MeterUnit.M3.value,
    )
    initial_value: Mapped[Decimal] = _meter_value(
        nullable=False, default=Decimal("0.000"), server_default=text("0.000")
    )
    max_monthly_delta: Mapped[Decimal] = _meter_value(
        nullable=False, default=Decimal("25.000"), server_default=text("25.000")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    @property
    def is_master_meter(self) -> bool:
        return self.apartment_id is None


class ReadingPeriod(Base):
    """One accounting month of a building (OPEN_FOR_RESIDENTS -> REVIEW_BY_BOARD -> LOCKED)."""

    __tablename__ = "meters_periods"
    __table_args__ = (
        UniqueConstraint("building_id", "period_code", name="uq_meters_periods_building_code"),
        UniqueConstraint("id", "building_id", name="uq_meters_periods_id_building"),
        CheckConstraint(
            "period_code ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_meters_periods_period_code"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False
    )
    period_code: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    status: Mapped[PeriodStatus] = mapped_column(
        varchar_enum(PeriodStatus, "ck_meters_periods_status"), nullable=False
    )
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MeterReading(Base):
    """The single active reading of a meter in a period (updates go through the audit log)."""

    __tablename__ = "meters_readings"
    __table_args__ = (
        UniqueConstraint("meter_id", "period_id", name="uq_meters_readings_meter_period"),
        # Meter and period must both belong to the reading's building.
        ForeignKeyConstraint(
            ["meter_id", "building_id"],
            ["meters_devices.id", "meters_devices.building_id"],
            name="fk_meters_readings_meter_building",
        ),
        ForeignKeyConstraint(
            ["period_id", "building_id"],
            ["meters_periods.id", "meters_periods.building_id"],
            name="fk_meters_readings_period_building",
        ),
        CheckConstraint("value >= 0", name="ck_meters_readings_value_non_negative"),
        CheckConstraint(
            "previous_value >= 0", name="ck_meters_readings_previous_value_non_negative"
        ),
        CheckConstraint(
            "consumption_delta = value - previous_value", name="ck_meters_readings_delta_consistent"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    building_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("property_buildings.id"), nullable=False
    )
    meter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    period_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    value: Mapped[Decimal] = _meter_value(nullable=False)
    previous_value: Mapped[Decimal] = _meter_value(nullable=False)
    consumption_delta: Mapped[Decimal] = _meter_value(nullable=False)
    source: Mapped[ReadingSource] = mapped_column(
        varchar_enum(ReadingSource, "ck_meters_readings_source"), nullable=False
    )
    is_anomaly_confirmed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=True
    )
    # "Timestamp of last modification": the ORM refreshes it on every UPDATE. A Core
    # INSERT ... ON CONFLICT DO UPDATE bypasses onupdate and must set it explicitly.
    recorded_at: Mapped[datetime] = _timestamp(onupdate=_utc_now)


class MeterReadingLog(Base):
    """Append-only audit trail of every insert/update of a reading (a DB trigger forbids
    UPDATE and DELETE on this table)."""

    __tablename__ = "meters_reading_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    reading_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meters_readings.id"), nullable=False, index=True
    )
    old_value: Mapped[Decimal | None] = _meter_value(nullable=True)
    new_value: Mapped[Decimal] = _meter_value(nullable=False)
    source: Mapped[ReadingSource] = mapped_column(
        varchar_enum(ReadingSource, "ck_meters_reading_logs_source"), nullable=False
    )
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("core_users.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_at: Mapped[datetime] = _timestamp()
