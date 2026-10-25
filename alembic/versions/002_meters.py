"""Meters: devices (apartment and building master meters), periods, readings, audit log.

Revision ID: 002
Revises: 001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
METER_VALUE = sa.Numeric(12, 3)
READING_SOURCES = "('RESIDENT', 'ADMIN_OVERRIDE', 'AUTO_ESTIMATED', 'IOT_WEBHOOK')"


def upgrade() -> None:
    op.create_table(
        "meters_devices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("building_id", UUID, sa.ForeignKey("property_buildings.id"), nullable=False),
        sa.Column("apartment_id", UUID, sa.ForeignKey("property_apartments.id"), nullable=True),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("serial_number", sa.String(100), nullable=False),
        sa.Column("location_label", sa.String(100), nullable=False, server_default="Main"),
        sa.Column("unit", sa.String(16), nullable=False, server_default="m3"),
        sa.Column("initial_value", METER_VALUE, nullable=False, server_default=sa.text("0.000")),
        sa.Column(
            "max_monthly_delta", METER_VALUE, nullable=False, server_default=sa.text("25.000")
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(
            "resource_type IN ('COLD_WATER', 'HOT_WATER', 'ELECTRICITY', 'HEAT', 'GAS')",
            name="ck_meters_devices_resource_type",
        ),
        sa.CheckConstraint("unit IN ('m3', 'kWh', 'MWh')", name="ck_meters_devices_unit"),
        sa.CheckConstraint(
            "initial_value >= 0", name="ck_meters_devices_initial_value_non_negative"
        ),
        sa.CheckConstraint(
            "max_monthly_delta > 0", name="ck_meters_devices_max_monthly_delta_positive"
        ),
    )
    op.create_index("ix_meters_devices_building_id", "meters_devices", ["building_id"])
    op.create_index("ix_meters_devices_apartment_id", "meters_devices", ["apartment_id"])

    op.create_table(
        "meters_periods",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("building_id", UUID, sa.ForeignKey("property_buildings.id"), nullable=False),
        sa.Column("period_code", sa.String(7), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("building_id", "period_code", name="uq_meters_periods_building_code"),
        sa.CheckConstraint(
            "period_code ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_meters_periods_period_code"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN_FOR_RESIDENTS', 'REVIEW_BY_BOARD', 'LOCKED')",
            name="ck_meters_periods_status",
        ),
    )
    op.create_index("ix_meters_periods_period_code", "meters_periods", ["period_code"])

    op.create_table(
        "meters_readings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("meter_id", UUID, sa.ForeignKey("meters_devices.id"), nullable=False),
        sa.Column("period_id", UUID, sa.ForeignKey("meters_periods.id"), nullable=False),
        sa.Column("value", METER_VALUE, nullable=False),
        sa.Column("previous_value", METER_VALUE, nullable=False),
        sa.Column("consumption_delta", METER_VALUE, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "is_anomaly_confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("submitted_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("meter_id", "period_id", name="uq_meters_readings_meter_period"),
        sa.CheckConstraint(f"source IN {READING_SOURCES}", name="ck_meters_readings_source"),
        sa.CheckConstraint("value >= 0", name="ck_meters_readings_value_non_negative"),
        sa.CheckConstraint(
            "previous_value >= 0", name="ck_meters_readings_previous_value_non_negative"
        ),
        sa.CheckConstraint(
            "consumption_delta = value - previous_value", name="ck_meters_readings_delta_consistent"
        ),
    )
    op.create_index("ix_meters_readings_meter_id", "meters_readings", ["meter_id"])
    op.create_index("ix_meters_readings_period_id", "meters_readings", ["period_id"])

    op.create_table(
        "meters_reading_logs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("reading_id", UUID, sa.ForeignKey("meters_readings.id"), nullable=False),
        sa.Column("old_value", METER_VALUE, nullable=True),
        sa.Column("new_value", METER_VALUE, nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("changed_by_user_id", UUID, sa.ForeignKey("core_users.id"), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"source IN {READING_SOURCES}", name="ck_meters_reading_logs_source"),
    )
    op.create_index("ix_meters_reading_logs_reading_id", "meters_reading_logs", ["reading_id"])

    # Append-only audit trail: no code path (or manual SQL) can rewrite history.
    op.execute(
        """
        CREATE FUNCTION meters_reading_logs_reject_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'meters_reading_logs is append-only (% rejected)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER meters_reading_logs_append_only
        BEFORE UPDATE OR DELETE ON meters_reading_logs
        FOR EACH ROW EXECUTE FUNCTION meters_reading_logs_reject_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER meters_reading_logs_append_only ON meters_reading_logs")
    op.execute("DROP FUNCTION meters_reading_logs_reject_change()")
    op.drop_table("meters_reading_logs")
    op.drop_table("meters_readings")
    op.drop_table("meters_periods")
    op.drop_table("meters_devices")
