"""The demo building (src/core/seed.py): seeded once, complete, and usable through the API."""

import asyncio
from decimal import Decimal

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import build_engine
from src.core.migrations import alembic_config
from src.core.security import DEMO_USER_HEADER
from src.core.seed import (
    DEMO_BUILDING_ID,
    DEMO_USERS,
    HISTORY_PERIODS,
    OPEN_PERIOD,
    DemoUserKey,
    demo_id,
    run_seed,
    seed_demo_data,
)
from src.modules.meters.models import MeterDevice, MeterReading, MeterReadingLog, ReadingPeriod
from src.modules.meters.schemas import PeriodStatus
from src.modules.property.models import Apartment, Building, User
from src.modules.property.schemas import BuildingRole
from tests.db_utils import scratch_database
from tests.factories import create_building

USERS = {demo.key: demo for demo in DEMO_USERS}


def as_demo(key: DemoUserKey) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(USERS[key].user_id)}


async def count(session: AsyncSession, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
async def seeded(db_session: AsyncSession) -> AsyncSession:
    assert await seed_demo_data(db_session) is True
    return db_session


# --- Seeding rules -------------------------------------------------------------------------------


async def test_the_demo_building_is_seeded_exactly_once(seeded: AsyncSession) -> None:
    assert await seed_demo_data(seeded) is False

    assert await count(seeded, Building) == 1
    building = await seeded.get(Building, DEMO_BUILDING_ID)
    assert building.name == "KÜ Sõpruse 12"
    assert await count(seeded, Apartment) == 6
    assert {a.entrance for a in await seeded.scalars(select(Apartment))} == {1, 2}
    assert await count(seeded, User) == len(DEMO_USERS)
    assert await count(seeded, ReadingPeriod) == 4


async def test_the_demo_building_has_its_association_details(
    api_client: AsyncClient, seeded: AsyncSession
) -> None:
    response = await api_client.get(
        "/api/v1/property/building", headers=as_demo(DemoUserKey.OWNER_14)
    )

    body = response.json()
    apartments = list(await seeded.scalars(select(Apartment)))
    assert body["registry_code"] == "80123456"
    assert body["address"] == "Sõpruse pst 12, Kristiine, 10615 Tallinn"
    assert (body["apartment_count"], body["entrance_count"]) == (6, 2)
    assert Decimal(body["heated_area_m2"]) == sum(a.area_m2 for a in apartments)


async def test_an_older_demo_building_gets_the_new_details_once(seeded: AsyncSession) -> None:
    building = await seeded.get(Building, DEMO_BUILDING_ID)
    building.registry_code, building.address = None, "Sõpruse pst 12, 10615 Tallinn"
    await seeded.flush()

    assert await seed_demo_data(seeded) is False
    assert (building.registry_code, building.address) == (
        "80123456",
        "Sõpruse pst 12, Kristiine, 10615 Tallinn",
    )

    # A code someone entered later is never overwritten.
    building.registry_code, building.address = "80999999", "Edited address"
    await seeded.flush()
    assert await seed_demo_data(seeded) is False
    assert (building.registry_code, building.address) == ("80999999", "Edited address")


async def test_a_database_with_data_is_never_seeded(db_session: AsyncSession) -> None:
    # A real association that switched SEED_DEMO_DATA on by mistake keeps its data alone.
    await create_building(db_session, name="Real association")

    assert await seed_demo_data(db_session) is False
    assert await db_session.get(Building, DEMO_BUILDING_ID) is None


async def test_every_role_of_the_switcher_has_a_user(seeded: AsyncSession) -> None:
    roles = set(await seeded.scalars(select(User.building_role)))
    labels = [demo.label for demo in DEMO_USERS]

    assert roles == set(BuildingRole)
    for required in ("Chairman", "Accountant", "Owner Apt 14", "Owner Apt 1", "Tenant Apt 2"):
        assert required in labels


async def test_history_is_locked_and_complete_and_october_is_open(seeded: AsyncSession) -> None:
    meters = await count(seeded, MeterDevice)
    periods = {p.period_code: p for p in await seeded.scalars(select(ReadingPeriod))}

    assert meters == 14  # 6 apartments x (cold + hot) + 2 master meters
    for code in HISTORY_PERIODS:
        assert periods[code].status == PeriodStatus.LOCKED
        assert await count(seeded, MeterReading, MeterReading.period_id == periods[code].id) == 14
    assert periods[OPEN_PERIOD].status == PeriodStatus.OPEN_FOR_RESIDENTS


async def test_every_reading_continues_the_previous_one_and_has_an_audit_trail(
    seeded: AsyncSession,
) -> None:
    rows = (
        await seeded.execute(
            select(MeterReading, ReadingPeriod.period_code, MeterDevice.initial_value)
            .join(ReadingPeriod, ReadingPeriod.id == MeterReading.period_id)
            .join(MeterDevice, MeterDevice.id == MeterReading.meter_id)
            .order_by(MeterReading.meter_id, ReadingPeriod.period_code)
        )
    ).all()
    expected_previous: dict = {}
    for reading, _code, initial in rows:
        assert reading.previous_value == expected_previous.get(reading.meter_id, initial)
        assert reading.value == reading.previous_value + reading.consumption_delta
        assert await count(seeded, MeterReadingLog, MeterReadingLog.reading_id == reading.id) >= 1
        expected_previous[reading.meter_id] = reading.value


# --- The seeded world through the API -------------------------------------------------------------


async def test_the_chairman_matrix_shows_every_status(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    september = await api_client.get(
        "/api/v1/meters/periods/2026-09/matrix", headers=as_demo(DemoUserKey.CHAIRMAN)
    )
    october = await api_client.get(
        "/api/v1/meters/periods/2026-10/matrix", headers=as_demo(DemoUserKey.CHAIRMAN)
    )

    assert september.status_code == october.status_code == 200
    assert {a["status"] for a in september.json()["apartments"]} == {"SUBMITTED"}
    for balance in september.json()["balances"]:
        assert Decimal(balance["unallocated_loss"]) > 0, "master meters show a small loss"
    by_number = {a["apartment_number"]: a["status"] for a in october.json()["apartments"]}
    assert by_number["1"] == "SUBMITTED"
    assert by_number["14"] == "MISSING"
    assert by_number["15"] == "ANOMALY_CONFIRMED"
    august = await api_client.get(
        "/api/v1/meters/periods/2026-08/matrix", headers=as_demo(DemoUserKey.CHAIRMAN)
    )
    statuses = {a["apartment_number"]: a["status"] for a in august.json()["apartments"]}
    assert statuses["13"] == "AUTO_ESTIMATED"


async def test_owner_14_can_submit_a_normal_reading_right_away(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    dashboard = await api_client.get(
        "/api/v1/meters/my-dashboard", headers=as_demo(DemoUserKey.OWNER_14)
    )
    (apartment,) = dashboard.json()["apartments"]
    cold = next(m for m in apartment["meters"] if m["resource_type"] == "COLD_WATER")
    value = Decimal(cold["previous_value"]) + Decimal("4.800")

    response = await api_client.post(
        "/api/v1/meters/readings",
        json={"meter_id": cold["meter_id"], "period_code": "2026-10", "value": f"{value}"},
        headers=as_demo(DemoUserKey.OWNER_14),
    )

    assert dashboard.json()["period"]["period_code"] == "2026-10"
    assert cold["reading"] is None
    assert response.status_code == 200, response.text
    assert response.json()["is_anomaly_confirmed"] is False


async def test_a_corrected_reading_has_two_history_entries(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    reading_id = demo_id(f"reading:{demo_id('meter:14:COLD_WATER')}:2026-09")

    history = await api_client.get(
        f"/api/v1/meters/readings/{reading_id}/history", headers=as_demo(DemoUserKey.OWNER_14)
    )

    assert [e["reason"] for e in history.json()["entries"]] == [
        "Resident submission",
        "Resident update",
    ]


async def test_the_emergency_reaches_entrance_2_only(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    url = "/api/v1/communication/emergencies/active"

    apt_14 = await api_client.get(url, headers=as_demo(DemoUserKey.OWNER_14))
    apt_2 = await api_client.get(url, headers=as_demo(DemoUserKey.TENANT_2))

    assert [e["category"] for e in apt_14.json()] == ["WATER_SHUTOFF"]
    assert apt_2.json() == []


async def test_the_forum_shows_each_residents_privacy_choice(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    feed = await api_client.get(
        "/api/v1/communication/posts",
        params={"post_type": "FORUM_TOPIC"},
        headers=as_demo(DemoUserKey.TENANT_2),
    )
    badges = {post["title"]: post["author_badge"]["display_label"] for post in feed.json()}

    assert badges == {
        "Giving away a children's bike": "Kati Mets (Apt. 1)",  # chose FULL_NAME
        "Shall we put a bike rack in the yard?": "Apt. 14",  # default APARTMENT_ONLY
    }
    assert "Ivan" not in feed.text
    assert "@demo.invalid" not in feed.text


# --- Demo users for the role switcher -------------------------------------------------------------


async def test_demo_users_list_the_switcher_without_names(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    response = await api_client.get("/api/v1/demo/users")

    assert response.status_code == 200
    assert [u["label"] for u in response.json()] == [demo.label for demo in DEMO_USERS]
    assert response.json()[3] == {
        "user_id": str(USERS[DemoUserKey.OWNER_14].user_id),
        "label": "Owner Apt 14",
        "building_role": "USER",
        "apartment_number": "14",
        "relation_type": "OWNER",
    }
    for demo in DEMO_USERS:
        assert demo.full_name not in response.text


async def test_demo_users_are_empty_without_seed_and_hidden_without_demo_mode(
    api_client: AsyncClient, db_app: FastAPI
) -> None:
    empty = await api_client.get("/api/v1/demo/users")
    db_app.dependency_overrides[get_settings] = lambda: Settings(DEMO_MODE_ENABLED=False)
    try:
        hidden = await api_client.get("/api/v1/demo/users")
    finally:
        db_app.dependency_overrides.pop(get_settings, None)

    assert empty.json() == []
    assert hidden.status_code == 404
    assert hidden.json()["error_code"] == "NOT_FOUND"


# --- Two containers starting at once --------------------------------------------------------------


async def test_concurrent_seeders_create_one_building(database_url: str) -> None:
    async with scratch_database(database_url) as url:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")

        results = await asyncio.gather(run_seed(url), run_seed(url))

        engine = build_engine(url)
        async with AsyncSession(engine) as session:
            buildings = await count(session, Building)
        await engine.dispose()

    assert sorted(results) == [False, True]
    assert buildings == 1


async def test_a_large_reading_of_owner_14_goes_through_both_popups(
    seeded: AsyncSession, api_client: AsyncClient
) -> None:
    # docs/API.md, section 5: above 3 x the 3-month average (14.4 m3 cold) the popups appear.
    meter_id = demo_id("meter:14:COLD_WATER")
    previous = (
        await seeded.scalars(
            select(MeterReading.value).where(
                MeterReading.meter_id == meter_id,
                MeterReading.period_id == demo_id("period:2026-09"),
            )
        )
    ).one()

    async def submit(delta: str, level: int):
        return await api_client.post(
            "/api/v1/meters/readings",
            json={
                "meter_id": str(meter_id),
                "period_code": "2026-10",
                "value": f"{previous + Decimal(delta)}",
                "confirmation_level": level,
            },
            headers=as_demo(DemoUserKey.OWNER_14),
        )

    step_1 = await submit("15.000", 0)
    step_2 = await submit("15.000", 1)
    saved = await submit("15.000", 2)

    assert step_1.json()["error_code"] == "ANOMALY_CONFIRMATION_STEP_1"
    assert step_1.json()["average_delta"] == "4.800"
    assert step_2.json()["error_code"] == "ANOMALY_CONFIRMATION_STEP_2"
    assert saved.status_code == 200
    assert saved.json()["is_anomaly_confirmed"] is True
