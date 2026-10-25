"""Integration tests of the meters API on a real PostgreSQL (fixture: tests/meters/conftest.py)."""

import csv
import io
import uuid
from dataclasses import fields
from decimal import Decimal

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.event_bus import event_bus
from src.core.security import DEMO_USER_HEADER
from src.modules.meters.events import MeterReadingSubmitted, ReadingPeriodStatusChanged
from src.modules.meters.models import MeterDevice, MeterReading, MeterReadingLog, ReadingPeriod
from src.modules.meters.schemas import MeterUnit, PeriodStatus, ReadingSource, ResourceType
from src.modules.property.models import ApartmentMembership
from tests.db_utils import failing_commit
from tests.factories import (
    create_apartment,
    create_building,
    create_membership,
    create_meter,
    create_period,
    create_reading,
    create_user,
)
from tests.meters.conftest import MetersWorld

API = "/api/v1/meters"


def as_user(user) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


async def submit(
    client: AsyncClient,
    user,
    meter,
    value: str,
    level: int = 0,
    period: str = "2026-10",
    reason: str | None = None,
) -> Response:
    return await client.post(
        f"{API}/readings",
        json={
            "meter_id": str(meter.id),
            "period_code": period,
            "value": value,
            "confirmation_level": level,
            "reason": reason,
        },
        headers=as_user(user),
    )


async def set_status(client: AsyncClient, user, status: str, period: str = "2026-10") -> Response:
    return await client.patch(
        f"{API}/periods/{period}/status", json={"status": status}, headers=as_user(user)
    )


async def log_count(session: AsyncSession, reading_id) -> int:
    return await session.scalar(
        select(func.count()).where(MeterReadingLog.reading_id == uuid.UUID(str(reading_id)))
    )


# --- Acceptance criterion (Task 4) --------------------------------------------------------------


async def test_full_period_lifecycle(api_client: AsyncClient, world: MetersWorld) -> None:
    """Resident submits -> updates -> 2 audit entries -> chairman closes -> estimates -> CSV."""
    first = await submit(api_client, world.owner_14, world.meter_14, "119.500")
    assert first.status_code == 200, first.text
    assert first.json()["consumption_delta"] == "4.500"
    reading_id = first.json()["reading_id"]

    second = await submit(api_client, world.owner_14, world.meter_14, "120.000")
    assert second.status_code == 200
    assert second.json() == {
        "reading_id": reading_id,
        "meter_id": str(world.meter_14.id),
        "period_code": "2026-10",
        "value": "120.000",
        "consumption_delta": "5.000",
        "source": "RESIDENT",
        "is_anomaly_confirmed": False,
    }

    history = await api_client.get(
        f"{API}/readings/{reading_id}/history", headers=as_user(world.owner_14)
    )
    entries = history.json()["entries"]
    assert [(e["old_value"], e["new_value"], e["reason"]) for e in entries] == [
        (None, "119.500", "Resident submission"),
        ("119.500", "120.000", "Resident update"),
    ]

    closed = await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    assert closed.json() == {
        "period_code": "2026-10",
        "previous_status": "OPEN_FOR_RESIDENTS",
        "status": "REVIEW_BY_BOARD",
    }

    estimate = await api_client.post(
        f"{API}/periods/2026-10/estimate-missing", headers=as_user(world.chairman)
    )
    assert estimate.status_code == 200
    assert estimate.json()["estimated_count"] == 2
    by_meter = {e["meter_id"]: e for e in estimate.json()["estimated"]}
    # Apt 2: last value 59.000 + 3-month average 3.000. Apt 5: no history -> previous + 0.000.
    assert (
        by_meter[str(world.meter_2.id)]["value"],
        by_meter[str(world.meter_2.id)]["consumption_delta"],
    ) == ("62.000", "3.000")
    assert (
        by_meter[str(world.meter_5.id)]["value"],
        by_meter[str(world.meter_5.id)]["consumption_delta"],
    ) == ("10.000", "0.000")

    export = await api_client.get(
        f"{API}/periods/2026-10/export-csv", headers=as_user(world.accountant)
    )
    assert export.status_code == 200
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    assert export.headers["content-disposition"] == 'attachment; filename="readings-2026-10.csv"'
    rows = list(csv.DictReader(io.StringIO(export.text.lstrip("﻿"))))
    assert [(r["entrance"], r["apartment_number"], r["value"], r["source"]) for r in rows] == [
        ("1", "2", "62.000", "AUTO_ESTIMATED"),
        ("1", "5", "10.000", "AUTO_ESTIMATED"),
        ("2", "14", "120.000", "RESIDENT"),
    ]


# --- Two-step anomaly confirmation --------------------------------------------------------------


async def test_anomaly_needs_two_confirmations_and_reports_the_numbers(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    # Apt 14: previous 115.000, average of 4, 5, 6 = 5.000, so 3x average = 15.000 < 25.000 limit.
    logs_before = await db_session.scalar(select(func.count()).select_from(MeterReadingLog))
    readings_before = await db_session.scalar(select(func.count()).select_from(MeterReading))
    step_1 = await submit(api_client, world.owner_14, world.meter_14, "1245.000", level=0)
    step_2 = await submit(api_client, world.owner_14, world.meter_14, "1245.000", level=1)

    assert step_1.status_code == step_2.status_code == 409
    assert step_1.json() == {
        "error_code": "ANOMALY_CONFIRMATION_STEP_1",
        "required_confirmation_level": 1,
        "consumption_delta": "1130.000",
        "average_delta": "5.000",
        "max_reasonable_delta": "25.000",
        "message": "Value is unusually high (consumption 1130.000 m3), are you sure?",
    }
    assert step_2.json() == {
        "error_code": "ANOMALY_CONFIRMATION_STEP_2",
        "required_confirmation_level": 2,
        "consumption_delta": "1130.000",
        "message": "Please double-check the digits; this goes directly to billing!",
    }
    # Warnings write nothing: no reading, no audit row.
    assert await db_session.scalar(select(func.count()).select_from(MeterReadingLog)) == logs_before
    assert (
        await db_session.scalar(select(func.count()).select_from(MeterReading)) == readings_before
    )

    saved = await submit(api_client, world.owner_14, world.meter_14, "1245.000", level=2)

    assert saved.status_code == 200
    assert saved.json()["is_anomaly_confirmed"] is True
    assert await log_count(db_session, saved.json()["reading_id"]) == 1


async def test_three_times_the_average_triggers_the_popup_below_the_meter_limit(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    # Delta 16.000: below the 25.000 limit, but above 3 x 5.000.
    response = await submit(api_client, world.owner_14, world.meter_14, "131.000")

    assert response.status_code == 409
    assert response.json()["error_code"] == "ANOMALY_CONFIRMATION_STEP_1"


async def test_value_below_previous_is_rejected(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    response = await submit(api_client, world.owner_14, world.meter_14, "114.000", level=2)

    assert response.status_code == 422
    assert response.json() == {
        "error_code": "READING_BELOW_PREVIOUS",
        "previous_value": "115.000",
        "submitted_value": "114.000",
        "message": "New reading cannot be lower than previous reading (115.000).",
    }


async def test_identical_resubmission_is_a_no_op(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    first = await submit(api_client, world.owner_14, world.meter_14, "119.000")
    received: list[MeterReadingSubmitted] = []

    async def handler(event: MeterReadingSubmitted) -> None:
        received.append(event)

    event_bus.subscribe(MeterReadingSubmitted, handler)
    try:
        again = await submit(api_client, world.owner_14, world.meter_14, "119.000")
    finally:
        event_bus.unsubscribe(MeterReadingSubmitted, handler)

    assert again.status_code == 200
    assert again.json() == first.json()
    assert await log_count(db_session, first.json()["reading_id"]) == 1
    assert received == [], "a no-op publishes nothing"


# --- Who may submit what, when ------------------------------------------------------------------


async def test_residents_cannot_see_other_apartments_or_master_meters(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    foreign = await submit(api_client, world.tenant_2, world.meter_14, "120.000")
    master = await submit(api_client, world.owner_14, world.master, "1040.000")
    unknown = await api_client.post(
        f"{API}/readings",
        json={"meter_id": str(uuid.uuid4()), "period_code": "2026-10", "value": "1.000"},
        headers=as_user(world.owner_14),
    )

    for response in (foreign, master, unknown):
        assert response.status_code == 404
        assert response.json()["error_code"] == "METER_NOT_FOUND"


async def test_member_without_permission_is_forbidden(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == world.tenant_2_membership.id)
        .values(can_submit_readings=False)
    )

    response = await submit(api_client, world.tenant_2, world.meter_2, "62.000")

    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"


async def test_residents_can_only_submit_while_the_period_is_open(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")

    response = await submit(api_client, world.owner_14, world.meter_14, "120.000")

    assert response.status_code == 409
    assert response.json()["error_code"] == "PERIOD_CLOSED"
    assert response.json()["period_status"] == "REVIEW_BY_BOARD"


async def test_board_override_in_review_needs_a_reason_for_existing_readings(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    resident = await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")

    without_reason = await submit(api_client, world.accountant, world.meter_14, "121.000")
    with_reason = await submit(
        api_client, world.accountant, world.meter_14, "121.000", reason="Photo shows 121"
    )

    assert without_reason.status_code == 422
    assert without_reason.json()["error_code"] == "REASON_REQUIRED"
    assert with_reason.status_code == 200
    assert with_reason.json()["source"] == "ADMIN_OVERRIDE"
    assert with_reason.json()["reading_id"] == resident.json()["reading_id"]
    reasons = list(
        await db_session.scalars(
            select(MeterReadingLog.reason)
            .where(MeterReadingLog.reading_id == uuid.UUID(resident.json()["reading_id"]))
            .order_by(MeterReadingLog.changed_at)
        )
    )
    assert reasons == ["Resident submission", "Photo shows 121"]


async def test_chairman_submits_master_meter_without_a_reason(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    response = await submit(api_client, world.chairman, world.master, "1045.000")

    assert response.status_code == 200
    assert response.json()["source"] == "ADMIN_OVERRIDE"
    assert response.json()["consumption_delta"] == "15.000"


@pytest.mark.parametrize("role", ["chairman", "accountant"])
async def test_locked_period_rejects_even_the_board(
    api_client: AsyncClient, world: MetersWorld, role: str
) -> None:
    response = await submit(
        api_client, getattr(world, role), world.meter_14, "116.000", period="2026-09", reason="fix"
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "PERIOD_LOCKED"


async def test_board_members_see_meters_but_cannot_submit(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    # 403, not 404: the board sees this meter in the matrix, so hiding it would be a lie.
    response = await submit(api_client, world.board, world.master, "1045.000")

    assert response.status_code == 403
    assert response.json()["error_code"] == "FORBIDDEN"


async def test_unknown_period_is_404(api_client: AsyncClient, world: MetersWorld) -> None:
    response = await submit(api_client, world.owner_14, world.meter_14, "120.000", period="2027-01")

    assert response.status_code == 404
    assert response.json()["error_code"] == "PERIOD_NOT_FOUND"


async def test_submission_publishes_an_event_without_personal_data(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    received: list[MeterReadingSubmitted] = []

    async def handler(event: MeterReadingSubmitted) -> None:
        received.append(event)

    event_bus.subscribe(MeterReadingSubmitted, handler)
    try:
        await submit(api_client, world.owner_14, world.meter_14, "120.000")
    finally:
        event_bus.unsubscribe(MeterReadingSubmitted, handler)

    (event,) = received
    assert event.meter_id == world.meter_14.id
    assert event.source == ReadingSource.RESIDENT
    assert str(event.consumption_delta) == "5.000"
    assert not {"full_name", "email", "phone"} & {f.name for f in fields(event)}


async def test_no_event_is_published_when_the_commit_fails(
    api_client: AsyncClient, world: MetersWorld, db_session: AsyncSession, published_events
) -> None:
    # Regression (#16 review): events were published before COMMIT, so a failed commit still
    # sent out a notification for a reading that was never saved.
    async with failing_commit(db_session, MeterReading):
        response = await submit(api_client, world.owner_14, world.meter_14, "120.000")

    assert response.status_code == 500
    assert published_events == []
    saved = await db_session.scalar(
        select(func.count())
        .select_from(MeterReading)
        .where(
            MeterReading.meter_id == world.meter_14.id,
            MeterReading.period_id == world.periods["2026-10"].id,
        )
    )
    assert saved == 0


# --- Period status ------------------------------------------------------------------------------


async def test_period_state_machine(api_client: AsyncClient, world: MetersWorld) -> None:
    received: list[ReadingPeriodStatusChanged] = []

    async def handler(event: ReadingPeriodStatusChanged) -> None:
        received.append(event)

    event_bus.subscribe(ReadingPeriodStatusChanged, handler)
    try:
        skip = await set_status(api_client, world.chairman, "LOCKED")
        steps = [
            await set_status(api_client, world.chairman, "REVIEW_BY_BOARD"),
            await set_status(api_client, world.accountant, "LOCKED"),
            await set_status(api_client, world.chairman, "REVIEW_BY_BOARD"),  # reopen
            await set_status(api_client, world.chairman, "OPEN_FOR_RESIDENTS"),
        ]
    finally:
        event_bus.unsubscribe(ReadingPeriodStatusChanged, handler)

    assert skip.status_code == 409
    assert skip.json() == {
        "error_code": "INVALID_PERIOD_TRANSITION",
        "message": "A period cannot go from OPEN_FOR_RESIDENTS to LOCKED.",
        "current_status": "OPEN_FOR_RESIDENTS",
        "requested_status": "LOCKED",
        "allowed_statuses": ["REVIEW_BY_BOARD"],
    }
    assert [s.status_code for s in steps] == [200, 200, 200, 200]
    assert [(e.old_status, e.new_status) for e in received] == [
        ("OPEN_FOR_RESIDENTS", "REVIEW_BY_BOARD"),
        ("REVIEW_BY_BOARD", "LOCKED"),
        ("LOCKED", "REVIEW_BY_BOARD"),
        ("REVIEW_BY_BOARD", "OPEN_FOR_RESIDENTS"),
    ]


async def test_only_chairman_and_accountant_manage_periods(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    for user in (world.board, world.owner_14):
        response = await set_status(api_client, user, "REVIEW_BY_BOARD")
        assert response.status_code == 403
        assert response.json()["error_code"] == "FORBIDDEN"


# --- Estimates ----------------------------------------------------------------------------------


async def test_estimates_only_during_review_and_only_once(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    url = f"{API}/periods/2026-10/estimate-missing"
    too_early = await api_client.post(url, headers=as_user(world.chairman))
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    first = await api_client.post(url, headers=as_user(world.accountant))
    second = await api_client.post(url, headers=as_user(world.accountant))
    board = await api_client.post(url, headers=as_user(world.board))

    assert too_early.status_code == 409
    assert too_early.json()["error_code"] == "PERIOD_NOT_IN_REVIEW"
    assert first.json()["estimated_count"] == 3  # nobody submitted in this test
    assert second.json() == {"period_code": "2026-10", "estimated_count": 0, "estimated": []}
    assert board.status_code == 403


async def test_estimate_history_ignores_estimates_and_confirmed_anomalies(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    # Rewrite apt 2 history: 07 normal (3), 08 estimate (40), 09 confirmed anomaly (200).
    readings = {
        r.period_id: r
        for r in await db_session.scalars(
            select(MeterReading).where(MeterReading.meter_id == world.meter_2.id)
        )
    }
    august = readings[world.periods["2026-08"].id]
    september = readings[world.periods["2026-09"].id]
    august.value, august.consumption_delta, august.source = (
        august.previous_value + 40,
        40,
        ReadingSource.AUTO_ESTIMATED,
    )
    september.previous_value = august.value
    september.value, september.consumption_delta = september.previous_value + 200, 200
    september.is_anomaly_confirmed = True
    await db_session.flush()

    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    response = await api_client.post(
        f"{API}/periods/2026-10/estimate-missing", headers=as_user(world.chairman)
    )

    estimate = next(
        e for e in response.json()["estimated"] if e["meter_id"] == str(world.meter_2.id)
    )
    # Only July's 3.000 counts as history: 293.000 (Sept value) + 3.000.
    assert (estimate["value"], estimate["consumption_delta"]) == ("296.000", "3.000")


# --- Chairman matrix ----------------------------------------------------------------------------


async def test_matrix_shows_statuses_and_the_master_balance(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await submit(api_client, world.chairman, world.master, "1045.000")  # delta 15
    await submit(api_client, world.owner_14, world.meter_14, "1245.000", level=2)  # anomaly
    await submit(api_client, world.tenant_2, world.meter_2, "62.000")  # delta 3

    response = await api_client.get(f"{API}/periods/2026-10/matrix", headers=as_user(world.board))

    assert response.status_code == 200
    body = response.json()
    assert body["period"]["status"] == "OPEN_FOR_RESIDENTS"
    assert body["balances"] == [
        {
            "resource_type": "COLD_WATER",
            "unit": "m3",
            "master_total": "15.000",
            "apartments_sum": "1133.000",
            "unallocated_loss": "-1118.000",  # negative: points at the wrong apartment reading
        }
    ]
    assert [(a["apartment_number"], a["entrance"], a["status"]) for a in body["apartments"]] == [
        ("2", 1, "SUBMITTED"),
        ("5", 1, "MISSING"),
        ("14", 2, "ANOMALY_CONFIRMED"),
    ]
    missing_cell = body["apartments"][1]["meters"][0]
    assert missing_cell["status"] == "MISSING" and missing_cell["reading"] is None
    for name in ("Ivan", "Petrov", "Mari", "Tamm", "Kuznetsov"):
        assert name not in response.text


async def test_matrix_balance_is_null_while_the_master_reading_is_missing(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")

    body = (
        await api_client.get(f"{API}/periods/2026-10/matrix", headers=as_user(world.chairman))
    ).json()

    assert body["balances"][0]["master_total"] is None
    assert body["balances"][0]["unallocated_loss"] is None
    assert body["balances"][0]["apartments_sum"] == "5.000"


async def test_residents_cannot_open_the_matrix(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    response = await api_client.get(
        f"{API}/periods/2026-10/matrix", headers=as_user(world.owner_14)
    )

    assert response.status_code == 403


# --- History ------------------------------------------------------------------------------------


async def test_history_masks_every_actor(api_client: AsyncClient, world: MetersWorld) -> None:
    resident = await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    await submit(api_client, world.chairman, world.meter_14, "121.000", reason="Photo")
    url = f"{API}/readings/{resident.json()['reading_id']}/history"

    response = await api_client.get(url, headers=as_user(world.accountant))

    assert [e["changed_by"] for e in response.json()["entries"]] == [
        {"display_label": "Apt. 14", "role_badge": "OWNER"},
        {"display_label": "Chairman", "role_badge": "BOARD"},
    ]
    for secret in ("Ivan", "Sidorov", str(world.owner_14.id), str(world.chairman.id)):
        assert secret not in response.text


async def test_history_is_hidden_from_other_residents(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    resident = await submit(api_client, world.owner_14, world.meter_14, "120.000")
    url = f"{API}/readings/{resident.json()['reading_id']}/history"

    neighbour = await api_client.get(url, headers=as_user(world.tenant_2))
    unknown = await api_client.get(
        f"{API}/readings/{uuid.uuid4()}/history", headers=as_user(world.chairman)
    )

    assert neighbour.status_code == unknown.status_code == 404
    assert neighbour.json() == unknown.json()


# --- Dashboard ----------------------------------------------------------------------------------


async def test_dashboard_shows_own_meters_previous_values_and_the_deadline(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")

    response = await api_client.get(f"{API}/my-dashboard", headers=as_user(world.owner_14))

    body = response.json()
    assert body["period"] == {
        "period_code": "2026-10",
        "status": "OPEN_FOR_RESIDENTS",
        "due_date": "2026-11-04T23:59:59Z",
    }
    (apartment,) = body["apartments"]
    assert apartment["apartment_number"] == "14" and apartment["can_submit_readings"] is True
    (meter,) = apartment["meters"]
    assert meter["meter_id"] == str(world.meter_14.id)
    assert meter["previous_value"] == "115.000"
    assert meter["reading"]["value"] == "120.000"


async def test_dashboard_for_an_earlier_period_and_unsubmitted_meters(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    october = (await api_client.get(f"{API}/my-dashboard", headers=as_user(world.owner_5))).json()
    july = (
        await api_client.get(
            f"{API}/my-dashboard",
            params={"period_code": "2026-07"},
            headers=as_user(world.owner_14),
        )
    ).json()

    october_meter = october["apartments"][0]["meters"][0]
    assert october_meter["reading"] is None
    assert october_meter["previous_value"] == "10.000"  # initial value, no history
    assert july["apartments"][0]["meters"][0]["previous_value"] == "100.000"


# --- CSV export ---------------------------------------------------------------------------------


async def test_csv_contains_master_meters_and_no_personal_data(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    response = await api_client.get(
        f"{API}/periods/2026-09/export-csv", headers=as_user(world.chairman)
    )

    assert response.text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("﻿"))))
    assert [(r["apartment_number"], r["previous_value"], r["value"]) for r in rows] == [
        ("2", "56.000", "59.000"),
        ("14", "109.000", "115.000"),
        ("MASTER", "1020.000", "1030.000"),
    ]
    assert rows[2]["entrance"] == ""
    for name in ("Ivan", "Petrov", "Mari", "Tamm"):
        assert name not in response.text


async def test_csv_neutralizes_spreadsheet_formulas(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    world_meter = world.meter_14
    await db_session.execute(
        update(type(world_meter))
        .where(type(world_meter).id == world_meter.id)
        .values(serial_number='=HYPERLINK("http://evil")', location_label="+cmd")
    )

    response = await api_client.get(
        f"{API}/periods/2026-09/export-csv", headers=as_user(world.chairman)
    )

    row = next(
        r
        for r in csv.DictReader(io.StringIO(response.text.lstrip("﻿")))
        if r["apartment_number"] == "14"
    )
    assert row["serial_number"] == '\'=HYPERLINK("http://evil")'
    assert row["location_label"] == "'+cmd"


async def test_residents_cannot_export(api_client: AsyncClient, world: MetersWorld) -> None:
    response = await api_client.get(
        f"{API}/periods/2026-10/export-csv", headers=as_user(world.owner_14)
    )

    assert response.status_code == 403


@pytest.mark.parametrize("bad", ["2026-13", "latest", "2026-1"])
async def test_invalid_period_codes_in_paths_are_validation_errors(
    api_client: AsyncClient, world: MetersWorld, bad: str
) -> None:
    response = await api_client.get(f"{API}/periods/{bad}/matrix", headers=as_user(world.chairman))

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


# --- Regressions from the independent review ----------------------------------------------------


async def test_control_characters_in_reason_are_a_validation_error_not_a_500(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    response = await submit(
        api_client, world.owner_14, world.meter_14, "120.000", reason="abc\u0000def"
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_matrix_exposes_master_meters_so_the_board_can_submit_them(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    body = (
        await api_client.get(f"{API}/periods/2026-10/matrix", headers=as_user(world.chairman))
    ).json()

    (master,) = body["master_meters"]
    assert master["meter_id"] == str(world.master.id)
    assert master["previous_value"] == "1030.000"
    assert master["status"] == "MISSING" and master["reading"] is None

    saved = await api_client.post(
        f"{API}/readings",
        json={"meter_id": master["meter_id"], "period_code": "2026-10", "value": "1045.000"},
        headers=as_user(world.accountant),
    )
    assert saved.status_code == 200


async def test_same_value_from_a_different_source_is_recorded(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    # Apt 5 has no history, so it is estimated at its previous value 10.000.
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    await api_client.post(
        f"{API}/periods/2026-10/estimate-missing", headers=as_user(world.chairman)
    )

    confirmed = await submit(
        api_client, world.chairman, world.meter_5, "10.000", reason="Photo confirms 10.000"
    )

    assert confirmed.status_code == 200
    assert confirmed.json()["source"] == "ADMIN_OVERRIDE"
    reasons = list(
        await db_session.scalars(
            select(MeterReadingLog.reason)
            .where(MeterReadingLog.reading_id == uuid.UUID(confirmed.json()["reading_id"]))
            .order_by(MeterReadingLog.changed_at)
        )
    )
    assert reasons == ["3-month avg estimate", "Photo confirms 10.000"]


async def test_residents_cannot_overwrite_a_board_correction(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    await submit(api_client, world.chairman, world.meter_14, "121.000", reason="Photo")
    await set_status(api_client, world.chairman, "OPEN_FOR_RESIDENTS")

    response = await submit(api_client, world.owner_14, world.meter_14, "125.000")

    assert response.status_code == 409
    assert response.json()["error_code"] == "READING_SET_BY_BOARD"


async def test_residents_may_replace_an_estimate_after_reopening(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    await api_client.post(
        f"{API}/periods/2026-10/estimate-missing", headers=as_user(world.chairman)
    )
    await set_status(api_client, world.chairman, "OPEN_FOR_RESIDENTS")

    response = await submit(api_client, world.owner_5, world.meter_5, "10.000")

    assert response.status_code == 200
    assert response.json()["source"] == "RESIDENT"


async def test_an_earlier_period_cannot_change_once_a_later_one_has_a_reading(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await db_session.execute(
        update(ReadingPeriod)
        .where(ReadingPeriod.id == world.periods["2026-09"].id)
        .values(status=PeriodStatus.REVIEW_BY_BOARD)
    )

    response = await submit(
        api_client, world.chairman, world.meter_14, "121.000", period="2026-09", reason="Typo"
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "LATER_READING_EXISTS"


async def test_whitespace_reason_counts_as_missing(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")

    response = await submit(api_client, world.chairman, world.meter_14, "121.000", reason="   ")

    assert response.status_code == 422
    assert response.json()["error_code"] == "REASON_REQUIRED"


async def test_staff_who_live_in_the_building_submit_as_residents_while_open(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    apartment_id = world.meter_14.apartment_id
    await db_session.execute(
        ApartmentMembership.__table__.insert().values(
            id=uuid.uuid4(),
            apartment_id=apartment_id,
            user_id=world.chairman.id,
            relation_type="OWNER",
        )
    )

    open_period = await submit(api_client, world.chairman, world.meter_14, "120.000")
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    in_review = await submit(api_client, world.chairman, world.meter_14, "121.000", reason="Fix")

    assert open_period.json()["source"] == "RESIDENT"
    assert in_review.json()["source"] == "ADMIN_OVERRIDE"


async def test_board_entry_for_an_apartment_in_an_open_period(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    response = await submit(api_client, world.accountant, world.meter_2, "62.000")

    assert response.status_code == 200
    assert response.json()["source"] == "ADMIN_OVERRIDE"


async def test_replaced_meters_accept_only_board_corrections_of_existing_readings(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await db_session.execute(
        update(MeterDevice).where(MeterDevice.id == world.meter_14.id).values(is_active=False)
    )

    resident = await submit(api_client, world.owner_14, world.meter_14, "121.000")
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    board = await submit(api_client, world.chairman, world.meter_14, "121.000", reason="Final")
    no_reading = await submit(
        api_client, world.chairman, world.meter_14, "116.000", period="2026-09"
    )

    assert resident.status_code == 404
    assert board.status_code == 200
    assert no_reading.status_code in (404, 409)  # Sept is LOCKED anyway


async def test_estimate_writes_audit_rows_without_a_submitter(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")
    estimate = await api_client.post(
        f"{API}/periods/2026-10/estimate-missing", headers=as_user(world.accountant)
    )
    reading_id = uuid.UUID(estimate.json()["estimated"][0]["reading_id"])

    stored = await db_session.get(MeterReading, reading_id)
    history = await api_client.get(
        f"{API}/readings/{reading_id}/history", headers=as_user(world.chairman)
    )

    assert stored.source == ReadingSource.AUTO_ESTIMATED
    assert stored.submitted_by_user_id is None
    (entry,) = history.json()["entries"]
    assert entry["reason"] == "3-month avg estimate"
    assert entry["changed_by"] == {"display_label": "Accountant", "role_badge": "ACCOUNTANT"}


async def test_estimate_that_would_overflow_numeric_is_a_clear_error(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    await db_session.execute(
        update(MeterReading)
        .where(
            MeterReading.meter_id == world.meter_2.id,
            MeterReading.period_id == world.periods["2026-09"].id,
        )
        .values(value=Decimal("999999998.000"), previous_value=Decimal("999999990.000"),
                consumption_delta=Decimal("8.000"))
    )  # fmt: skip
    await set_status(api_client, world.chairman, "REVIEW_BY_BOARD")

    response = await api_client.post(
        f"{API}/periods/2026-10/estimate-missing", headers=as_user(world.chairman)
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "ESTIMATE_OUT_OF_RANGE"
    assert response.json()["meter_id"] == str(world.meter_2.id)


async def test_average_window_takes_the_last_three_qualifying_readings(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    # June delta 1 (qualifies), July 4, Aug 5, Sept turned into an estimate (skipped):
    # the window reaches back to June: mean(1, 4, 5) = 3.333.
    june = await create_period(db_session, world.building, "2026-06", PeriodStatus.LOCKED)
    await create_reading(db_session, world.meter_14, june, Decimal("100.000"), Decimal("99.000"))
    await db_session.execute(
        update(MeterReading)
        .where(
            MeterReading.meter_id == world.meter_14.id,
            MeterReading.period_id == world.periods["2026-09"].id,
        )
        .values(source=ReadingSource.AUTO_ESTIMATED)
    )

    response = await submit(api_client, world.owner_14, world.meter_14, "1245.000")

    assert response.json()["average_delta"] == "3.333"


async def test_submission_span_records_the_outcome_of_every_exit(
    api_client: AsyncClient, span_exporter, world: MetersWorld
) -> None:
    await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await submit(api_client, world.owner_14, world.meter_14, "120.000")
    await submit(api_client, world.tenant_2, world.meter_14, "120.000")
    await submit(api_client, world.owner_14, world.meter_14, "1245.000")

    outcomes = [
        span.attributes["meters.outcome"]
        for span in span_exporter.get_finished_spans()
        if span.name == "meters.submit_reading"
    ]
    assert outcomes == ["created", "unchanged", "METER_NOT_FOUND", "ANOMALY_CONFIRMATION_STEP_1"]


async def test_matrix_balances_several_masters_resource_types_and_units(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    october = world.periods["2026-10"]
    second_master = await create_meter(db_session, world.building, None)
    hot_master = await create_meter(db_session, world.building, None, ResourceType.HOT_WATER)
    heat_kwh = await create_meter(db_session, world.building, None, ResourceType.HEAT)
    heat_mwh = await create_meter(db_session, world.building, None, ResourceType.HEAT)
    for meter, unit in ((heat_kwh, MeterUnit.KWH), (heat_mwh, MeterUnit.MWH)):
        await db_session.execute(
            update(MeterDevice).where(MeterDevice.id == meter.id).values(unit=unit)
        )
    for meter, value in (
        (world.master, "1040.000"), (second_master, "7.000"), (hot_master, "3.000"),
        (heat_kwh, "100.000"), (heat_mwh, "1.000"),
    ):  # fmt: skip
        previous = Decimal("1030.000") if meter is world.master else Decimal("0.000")
        await create_reading(db_session, meter, october, Decimal(value), previous)
    await create_reading(
        db_session, world.meter_14, october, Decimal("120.000"), Decimal("115.000")
    )

    balances = (
        await api_client.get(f"{API}/periods/2026-10/matrix", headers=as_user(world.chairman))
    ).json()["balances"]

    summary = {
        (b["resource_type"], b["unit"]): (b["master_total"], b["unallocated_loss"])
        for b in balances
    }
    assert summary == {
        ("COLD_WATER", "m3"): ("17.000", "12.000"),  # two masters 10 + 7, apartments 5
        ("HOT_WATER", "m3"): ("3.000", "3.000"),
        ("HEAT", "kWh"): ("100.000", "100.000"),
        ("HEAT", "MWh"): ("1.000", "1.000"),  # never summed with kWh
    }


async def test_master_meter_history_is_hidden_from_residents(
    api_client: AsyncClient, world: MetersWorld
) -> None:
    saved = await submit(api_client, world.chairman, world.master, "1045.000")
    url = f"{API}/readings/{saved.json()['reading_id']}/history"

    assert (await api_client.get(url, headers=as_user(world.owner_14))).status_code == 404
    assert (await api_client.get(url, headers=as_user(world.board))).status_code == 200


async def test_dashboard_without_any_period(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    building = await create_building(db_session, name="KÜ Uus 1")
    apartment = await create_apartment(db_session, building, "1")
    owner = await create_user(db_session, building, "New Owner")
    await create_membership(db_session, owner, apartment)
    await create_meter(db_session, building, apartment, initial_value=Decimal("5.000"))

    body = (await api_client.get(f"{API}/my-dashboard", headers=as_user(owner))).json()

    assert body["period"] is None
    assert body["apartments"][0]["meters"][0]["previous_value"] == "5.000"


async def test_dashboard_defaults_to_the_open_period_not_a_pre_created_one(
    api_client: AsyncClient, db_session: AsyncSession, world: MetersWorld
) -> None:
    december = await create_period(db_session, world.building, "2026-12", PeriodStatus.LOCKED)
    assert december.period_code > "2026-10"

    body = (await api_client.get(f"{API}/my-dashboard", headers=as_user(world.owner_14))).json()

    assert body["period"]["period_code"] == "2026-10"


async def test_export_without_the_csv_plugin_is_a_clear_404(
    api_client: AsyncClient, db_app, world: MetersWorld
) -> None:
    from src.plugins.registry import PluginRegistry

    original = db_app.state.plugin_registry
    db_app.state.plugin_registry = PluginRegistry()
    try:
        response = await api_client.get(
            f"{API}/periods/2026-10/export-csv", headers=as_user(world.chairman)
        )
    finally:
        db_app.state.plugin_registry = original

    assert response.status_code == 404
    assert response.json()["error_code"] == "EXPORTER_NOT_ENABLED"
