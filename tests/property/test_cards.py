"""Apartment cards for the board (epic E8, task P1): relations and counts, never people."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import DEMO_USER_HEADER
from src.modules.property.models import ApartmentMembership, User
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType
from tests.factories import (
    DemoBuilding,
    create_apartment,
    create_building,
    create_demo_building,
    create_membership,
    create_user,
)

API = "/api/v1/property/apartments"


def as_user(user: User) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


@pytest.fixture
async def demo(db_session: AsyncSession) -> DemoBuilding:
    return await create_demo_building(db_session)


async def test_board_lists_every_apartment_naturally_with_member_counts(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding
) -> None:
    board = await create_user(db_session, demo.building, "Peeter Saar", role=BuildingRole.BOARD)
    await create_apartment(db_session, demo.building, "10", entrance=1, floor=3)  # empty
    family = await create_user(db_session, demo.building, "Liis Petrova")
    await create_membership(db_session, family, demo.apt_14, relation=RelationType.RESIDENT)

    for viewer in (demo.chairman, board):
        response = await api_client.get(API, headers=as_user(viewer))
        assert response.status_code == 200
        rows = response.json()["apartments"]
        assert [(row["entrance"], row["apartment_number"]) for row in rows] == [
            (1, "2"),
            (1, "10"),
            (2, "14"),
        ]
        by_number = {row["apartment_number"]: row for row in rows}
        assert by_number["2"] == {
            "apartment_id": str(demo.apt_2.id),
            "apartment_number": "2",
            "entrance": 1,
            "floor": 1,
            "area_m2": "54.30",
            "owner_count": 0,
            "tenant_count": 1,
            "resident_count": 0,
        }
        assert (by_number["10"]["owner_count"], by_number["10"]["tenant_count"]) == (0, 0)
        assert (by_number["14"]["owner_count"], by_number["14"]["resident_count"]) == (1, 1)


async def test_card_shows_relations_and_dates_without_people(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding
) -> None:
    # The owner consents to the full name on the forum: the card still shows no name.
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == demo.owner_14_membership.id)
        .values(
            privacy_display_mode=DisplayMode.FULL_NAME,
            started_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
        )
    )
    tenant = await create_user(
        db_session, demo.building, "Kalle Kask", email="kalle@example.com", phone="+372 5222 3333"
    )
    tenant_membership = await create_membership(
        db_session, tenant, demo.apt_14, relation=RelationType.TENANT
    )

    response = await api_client.get(f"{API}/{demo.apt_14.id}", headers=as_user(demo.chairman))

    assert response.status_code == 200
    card = response.json()
    assert card["apartment_number"] == "14" and card["entrance"] == 2 and card["floor"] == 4
    assert (card["owner_count"], card["tenant_count"], card["resident_count"]) == (1, 1, 0)
    assert card["residents"] == [
        {"relation_type": "OWNER", "since": "2026-10-01T09:00:00Z"},
        {
            "relation_type": "TENANT",
            "since": tenant_membership.started_at.isoformat().replace("+00:00", "Z"),
        },
    ]
    raw = response.text
    for secret in (
        demo.owner_14.full_name,
        "Ivan",
        "Petrov",
        demo.owner_14.email or "",
        demo.owner_14.phone or "",
        "Kalle",
        "kalle@example.com",
        "+372 5222 3333",
        str(demo.owner_14.id),
        str(tenant.id),
        str(demo.owner_14_membership.id),
        str(tenant_membership.id),
    ):
        assert secret not in raw, secret
    for key in ('"full_name"', '"email"', '"phone"', '"user_id"', '"display_label"'):
        assert key not in raw


async def test_card_leaves_out_ended_memberships_and_inactive_accounts(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding
) -> None:
    gone = await create_user(db_session, demo.building, "Former Tenant")
    gone_membership = await create_membership(
        db_session, gone, demo.apt_2, relation=RelationType.TENANT
    )
    closed = await create_user(db_session, demo.building, "Closed Account", is_active=False)
    await create_membership(db_session, closed, demo.apt_2, relation=RelationType.TENANT)
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == gone_membership.id)
        .values(ended_at=datetime.now(UTC) - timedelta(days=1))
    )

    card = (await api_client.get(f"{API}/{demo.apt_2.id}", headers=as_user(demo.chairman))).json()
    listed = (await api_client.get(API, headers=as_user(demo.chairman))).json()["apartments"]

    assert [resident["relation_type"] for resident in card["residents"]] == ["TENANT"]
    assert card["tenant_count"] == 1
    assert next(row for row in listed if row["apartment_number"] == "2")["tenant_count"] == 1


async def test_owners_come_first_then_tenants_then_family_each_by_start(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding
) -> None:
    family = await create_user(db_session, demo.building, "Family Member")
    await create_membership(db_session, family, demo.apt_2, relation=RelationType.RESIDENT)
    co_owner = await create_user(db_session, demo.building, "Co Owner")
    await create_membership(db_session, co_owner, demo.apt_2, relation=RelationType.OWNER)
    second_owner = await create_user(db_session, demo.building, "Second Owner")
    second = await create_membership(db_session, second_owner, demo.apt_2)
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == second.id)
        .values(started_at=datetime(2020, 1, 1, tzinfo=UTC))
    )

    card = (await api_client.get(f"{API}/{demo.apt_2.id}", headers=as_user(demo.chairman))).json()

    assert [resident["relation_type"] for resident in card["residents"]] == [
        "OWNER",
        "OWNER",
        "TENANT",
        "RESIDENT",
    ]
    assert card["residents"][0]["since"] == "2020-01-01T00:00:00Z"


@pytest.mark.parametrize("role", [BuildingRole.ACCOUNTANT, BuildingRole.USER])
async def test_only_the_board_sees_cards(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding, role: BuildingRole
) -> None:
    user = await create_user(db_session, demo.building, "Someone Else", role=role)
    if role == BuildingRole.USER:
        await create_membership(db_session, user, demo.apt_14)

    for path in (API, f"{API}/{demo.apt_14.id}"):
        response = await api_client.get(path, headers=as_user(user))
        assert response.status_code == 403
        assert response.json()["error_code"] == "FORBIDDEN"


async def test_cards_stay_inside_the_callers_building(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding
) -> None:
    other = await create_building(db_session, "Other Association")
    foreign = await create_apartment(db_session, other, "1")

    listed = (await api_client.get(API, headers=as_user(demo.chairman))).json()["apartments"]
    assert str(foreign.id) not in {row["apartment_id"] for row in listed}
    for apartment_id in (foreign.id, uuid.uuid4()):
        response = await api_client.get(f"{API}/{apartment_id}", headers=as_user(demo.chairman))
        assert response.status_code == 404
        assert response.json()["error_code"] == "APARTMENT_NOT_FOUND"


async def test_cards_require_authentication(api_client: AsyncClient) -> None:
    assert (await api_client.get(API)).status_code == 401
