"""An apartment's readings across periods (epic E8, task P1; fixture: tests/meters/conftest.py)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import DEMO_USER_HEADER
from src.modules.meters import service as meters_service
from src.modules.meters.models import MeterDevice
from src.modules.meters.schemas import ReadingSource
from src.modules.property.models import ApartmentMembership, User
from tests.factories import (
    create_apartment,
    create_building,
    create_meter,
    create_reading,
)
from tests.meters.conftest import MetersWorld

D = Decimal


def as_user(user: User) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


def readings_url(meter: MeterDevice) -> str:
    return f"/api/v1/meters/apartments/{meter.apartment_id}/readings"


async def test_staff_see_an_apartments_readings_newest_first(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    for viewer in (world.chairman, world.board, world.accountant):
        response = await api_client.get(readings_url(world.meter_14), headers=as_user(viewer))
        assert response.status_code == 200
        body = response.json()
        assert body["apartment_id"] == str(world.meter_14.apartment_id)
        assert body["apartment_number"] == "14"
        assert [period["period_code"] for period in body["periods"]] == [
            "2026-10",
            "2026-09",
            "2026-08",
            "2026-07",
        ]
        (meter,) = body["meters"]
        assert meter["meter_id"] == str(world.meter_14.id)
        assert meter["is_active"] is True and meter["unit"] == "m3"
        # 2026-10 has no reading yet, so it is not in the meter's list.
        assert [
            (reading["period_code"], reading["value"], reading["consumption_delta"])
            for reading in meter["readings"]
        ] == [
            ("2026-09", "115.000", "6.000"),
            ("2026-08", "109.000", "5.000"),
            ("2026-07", "104.000", "4.000"),
        ]
        assert meter["readings"][0]["source"] == "RESIDENT"


async def test_a_resident_sees_only_their_own_apartment(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    # The history belongs to the apartment: the tenant sees readings from before moving in.
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == world.tenant_2_membership.id)
        .values(started_at=datetime.now(UTC) + timedelta(days=1))
    )
    own = await api_client.get(readings_url(world.meter_2), headers=as_user(world.tenant_2))
    assert own.status_code == 200
    assert len(own.json()["meters"][0]["readings"]) == 3

    for user, meter in ((world.tenant_2, world.meter_14), (world.owner_5, world.meter_2)):
        response = await api_client.get(readings_url(meter), headers=as_user(user))
        assert response.status_code == 404
        assert response.json()["error_code"] == "APARTMENT_NOT_FOUND"


async def test_a_former_resident_loses_access(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == world.tenant_2_membership.id)
        .values(ended_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    response = await api_client.get(readings_url(world.meter_2), headers=as_user(world.tenant_2))

    assert response.status_code == 404
    assert response.json()["error_code"] == "APARTMENT_NOT_FOUND"


async def test_unknown_and_foreign_apartments_are_not_found(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    other = await create_building(db_session, "Other Association")
    foreign = await create_apartment(db_session, other, "1")

    for apartment_id in (foreign.id, uuid.uuid4()):
        response = await api_client.get(
            f"/api/v1/meters/apartments/{apartment_id}/readings", headers=as_user(world.chairman)
        )
        assert response.status_code == 404
        assert response.json()["error_code"] == "APARTMENT_NOT_FOUND"


async def test_replaced_meters_stay_while_they_have_readings_in_the_window(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    apartment_id = world.meter_14.apartment_id
    old_without_readings = await create_meter(db_session, world.building, None)
    await db_session.execute(
        update(MeterDevice)
        .where(MeterDevice.id.in_([world.meter_14.id, old_without_readings.id]))
        .values(is_active=False, apartment_id=apartment_id)
    )
    replacement = await create_meter(db_session, world.building, None, initial_value=D("0.000"))
    await db_session.execute(
        update(MeterDevice)
        .where(MeterDevice.id == replacement.id)
        .values(apartment_id=apartment_id)
    )
    await create_reading(
        db_session,
        replacement,
        world.periods["2026-10"],
        D("3.500"),
        D("0.000"),
        source=ReadingSource.AUTO_ESTIMATED,
    )

    body = (
        await api_client.get(readings_url(world.meter_14), headers=as_user(world.owner_14))
    ).json()

    meters = {meter["meter_id"]: meter for meter in body["meters"]}
    assert set(meters) == {str(world.meter_14.id), str(replacement.id)}
    assert meters[str(world.meter_14.id)]["is_active"] is False
    assert len(meters[str(world.meter_14.id)]["readings"]) == 3
    (estimate,) = meters[str(replacement.id)]["readings"]
    assert estimate["period_code"] == "2026-10" and estimate["source"] == "AUTO_ESTIMATED"


async def test_only_the_latest_periods_are_returned(
    api_client: AsyncClient,
    db_session: AsyncSession,
    world: MetersWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meters_service, "APARTMENT_HISTORY_PERIODS", 2)

    # A replaced meter whose only reading is older than the window is left out.
    old = await create_meter(db_session, world.building, None)
    await db_session.execute(
        update(MeterDevice)
        .where(MeterDevice.id == old.id)
        .values(is_active=False, apartment_id=world.meter_14.apartment_id)
    )
    await create_reading(db_session, old, world.periods["2026-07"], D("1.000"), D("0.000"))

    body = (
        await api_client.get(readings_url(world.meter_14), headers=as_user(world.chairman))
    ).json()

    assert [period["period_code"] for period in body["periods"]] == ["2026-10", "2026-09"]
    assert [meter["meter_id"] for meter in body["meters"]] == [str(world.meter_14.id)]
    assert [reading["period_code"] for reading in body["meters"][0]["readings"]] == ["2026-09"]


async def test_the_payload_has_no_people(api_client: AsyncClient, world: MetersWorld) -> None:
    raw = (await api_client.get(readings_url(world.meter_14), headers=as_user(world.chairman))).text

    for secret in ("Ivan", "Petrov", "+372 5555 1234", str(world.owner_14.id)):
        assert secret not in raw
    assert '"submitted_by' not in raw
