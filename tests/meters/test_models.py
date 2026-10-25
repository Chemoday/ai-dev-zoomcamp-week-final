"""Integration tests: the meters schema itself protects the data (real PostgreSQL)."""

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.meters.models import MeterDevice, MeterReading, MeterReadingLog
from src.modules.meters.schemas import MeterUnit, ReadingSource
from tests.factories import (
    create_apartment,
    create_building,
    create_meter,
    create_period,
    create_reading,
)

D = Decimal


@pytest.fixture
async def setup(db_session: AsyncSession):
    building = await create_building(db_session)
    apartment = await create_apartment(db_session, building, "14", entrance=2)
    meter = await create_meter(db_session, building, apartment, initial_value=D("120.500"))
    period = await create_period(db_session, building, "2026-10")
    return building, apartment, meter, period


async def expect_rejected(session: AsyncSession, statement, params=None, match: str = "") -> None:
    """Run SQL that PostgreSQL must refuse, then restore the session for the next assertion."""
    with pytest.raises((IntegrityError, DBAPIError), match=match):
        await session.execute(text(statement), params or {})
    await session.rollback()


# --- Devices ------------------------------------------------------------------------------------


async def test_master_meter_has_no_apartment(db_session: AsyncSession, setup) -> None:
    building, _, apartment_meter, _ = setup
    master = await create_meter(db_session, building, None)

    rows = (
        await db_session.execute(
            select(MeterDevice.id, MeterDevice.apartment_id).where(
                MeterDevice.building_id == building.id
            )
        )
    ).all()

    assert master.is_master_meter and not apartment_meter.is_master_meter
    assert {(row.id, row.apartment_id) for row in rows} == {
        (master.id, None),
        (apartment_meter.id, apartment_meter.apartment_id),
    }


async def test_device_server_defaults(db_session: AsyncSession, setup) -> None:
    building = setup[0]
    meter_id = await db_session.scalar(
        text(
            "INSERT INTO meters_devices (id, building_id, resource_type, serial_number) "
            "VALUES (gen_random_uuid(), :building, 'HOT_WATER', 'SN-1') RETURNING id"
        ),
        {"building": building.id},
    )
    meter = await db_session.get(MeterDevice, meter_id)

    assert meter.location_label == "Main"
    assert meter.unit == MeterUnit.M3
    assert meter.initial_value == D("0.000")
    assert meter.max_monthly_delta == D("25.000")
    assert meter.is_active is True


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("resource_type", "STEAM", "ck_meters_devices_resource_type"),
        ("unit", "liters", "ck_meters_devices_unit"),
        ("initial_value", "-1", "ck_meters_devices_initial_value_non_negative"),
        ("max_monthly_delta", "0", "ck_meters_devices_max_monthly_delta_positive"),
    ],
)
async def test_device_constraints(
    db_session: AsyncSession, setup, column: str, value: str, constraint: str
) -> None:
    meter = setup[2]
    await expect_rejected(
        db_session,
        f"UPDATE meters_devices SET {column} = :value WHERE id = :id",
        {"value": value, "id": meter.id},
        match=constraint,
    )


async def test_apartment_meter_must_be_in_the_apartments_building(
    db_session: AsyncSession, setup
) -> None:
    _, apartment, meter, _ = setup
    other = await create_building(db_session, name="KÜ Mujal 1")

    await expect_rejected(
        db_session,
        "UPDATE meters_devices SET building_id = :other WHERE id = :id",
        {"other": other.id, "id": meter.id},
        match="fk_meters_devices_apartment_building",
    )


# --- Periods ------------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["2026-13", "2026-00", "2026-1x", "20261-0"])
async def test_period_code_format_is_enforced(db_session: AsyncSession, setup, code: str) -> None:
    period = setup[3]
    await expect_rejected(
        db_session,
        "UPDATE meters_periods SET period_code = :code WHERE id = :id",
        {"code": code, "id": period.id},
        match="ck_meters_periods_period_code",
    )


async def test_one_period_per_month_per_building(db_session: AsyncSession, setup) -> None:
    building = setup[0]
    with pytest.raises(IntegrityError, match="uq_meters_periods_building_code"):
        await create_period(db_session, building, "2026-10")
    await db_session.rollback()


async def test_period_status_is_enforced(db_session: AsyncSession, setup) -> None:
    await expect_rejected(
        db_session,
        "UPDATE meters_periods SET status = 'CLOSED' WHERE id = :id",
        {"id": setup[3].id},
        match="ck_meters_periods_status",
    )


# --- Readings -----------------------------------------------------------------------------------


async def test_reading_values_round_trip_exactly(db_session: AsyncSession, setup) -> None:
    _, _, meter, period = setup
    reading = await create_reading(db_session, meter, period, D("999999999.999"), D("120.500"))
    db_session.expunge_all()

    stored = await db_session.get(MeterReading, reading.id)

    assert stored.value == D("999999999.999")
    assert stored.consumption_delta == D("999999879.499")
    assert isinstance(stored.value, Decimal)


async def test_one_reading_per_meter_per_period(db_session: AsyncSession, setup) -> None:
    _, _, meter, period = setup
    await create_reading(db_session, meter, period, D("125.000"), D("120.500"))

    with pytest.raises(IntegrityError, match="uq_meters_readings_meter_period"):
        await create_reading(db_session, meter, period, D("126.000"), D("120.500"))
    await db_session.rollback()


async def test_stored_delta_must_match_the_values(db_session: AsyncSession, setup) -> None:
    _, _, meter, period = setup
    reading = await create_reading(db_session, meter, period, D("125.000"), D("120.500"))

    await expect_rejected(
        db_session,
        "UPDATE meters_readings SET consumption_delta = 1 WHERE id = :id",
        {"id": reading.id},
        match="ck_meters_readings_delta_consistent",
    )


@pytest.mark.parametrize(
    ("assignment", "constraint"),
    [
        ("value = -1, consumption_delta = -121.5", "ck_meters_readings_value_non_negative"),
        (
            "previous_value = -1, consumption_delta = 126",
            "ck_meters_readings_previous_value_non_negative",
        ),
        ("source = 'GUESS'", "ck_meters_readings_source"),
    ],
)
async def test_reading_constraints(
    db_session: AsyncSession, setup, assignment: str, constraint: str
) -> None:
    _, _, meter, period = setup
    reading = await create_reading(db_session, meter, period, D("125.000"), D("120.500"))

    await expect_rejected(
        db_session,
        f"UPDATE meters_readings SET {assignment} WHERE id = :id",
        {"id": reading.id},
        match=constraint,
    )


# --- Audit log ----------------------------------------------------------------------------------


async def test_audit_log_is_append_only(db_session: AsyncSession, setup) -> None:
    _, _, meter, period = setup
    reading = await create_reading(db_session, meter, period, D("125.000"), D("120.500"))
    log = MeterReadingLog(
        reading_id=reading.id,
        old_value=None,
        new_value=D("125.000"),
        source=ReadingSource.RESIDENT,
        reason="Resident submission",
    )
    db_session.add(log)
    await db_session.flush()
    await db_session.commit()  # keep the row when the rejected statements roll back
    log_id = log.id  # the rollbacks below expire ORM objects

    await expect_rejected(
        db_session,
        "UPDATE meters_reading_logs SET new_value = 1 WHERE id = :id",
        {"id": log_id},
        match="append-only",
    )
    await expect_rejected(
        db_session,
        "DELETE FROM meters_reading_logs WHERE id = :id",
        {"id": log_id},
        match="append-only",
    )
    remaining = await db_session.scalar(
        select(MeterReadingLog.new_value).where(MeterReadingLog.id == log_id)
    )
    assert remaining == D("125.000")


async def test_audit_log_cannot_be_truncated(db_session: AsyncSession, setup) -> None:
    await expect_rejected(db_session, "TRUNCATE meters_reading_logs", match="append-only")


# --- One building per row chain (regressions from the independent review) ----------------------


async def test_reading_cannot_join_a_meter_and_a_period_of_different_buildings(
    db_session: AsyncSession, setup
) -> None:
    building, _, meter, _ = setup
    other = await create_building(db_session, name="KÜ Mujal 1")
    foreign_period = await create_period(db_session, other, "2026-10")
    params = {"building": building.id, "meter": meter.id, "period": foreign_period.id}

    await expect_rejected(
        db_session,
        "INSERT INTO meters_readings (id, building_id, meter_id, period_id, value, previous_value,"
        " consumption_delta, source) VALUES (gen_random_uuid(), :building, :meter, :period,"
        " 125, 120, 5, 'RESIDENT')",
        params,
        match="fk_meters_readings_period_building",
    )


async def test_reading_building_must_be_the_meters_building(
    db_session: AsyncSession, setup
) -> None:
    _, _, meter, _ = setup
    other = await create_building(db_session, name="KÜ Mujal 1")
    other_period = await create_period(db_session, other, "2026-10")
    params = {"building": other.id, "meter": meter.id, "period": other_period.id}

    await expect_rejected(
        db_session,
        "INSERT INTO meters_readings (id, building_id, meter_id, period_id, value, previous_value,"
        " consumption_delta, source) VALUES (gen_random_uuid(), :building, :meter, :period,"
        " 125, 120, 5, 'RESIDENT')",
        params,
        match="fk_meters_readings_meter_building",
    )


async def test_recorded_at_is_refreshed_on_update(db_session: AsyncSession, setup) -> None:
    _, _, meter, period = setup
    reading = await create_reading(db_session, meter, period, D("125.000"), D("120.500"))
    first = reading.recorded_at

    reading.value = D("126.000")
    reading.consumption_delta = D("5.500")
    await db_session.flush()

    assert reading.recorded_at > first
