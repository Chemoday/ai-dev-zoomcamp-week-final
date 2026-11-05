"""Ended memberships and closed accounts are history (docs/ARCHITECTURE.md 4.3, task E7/A4): a
test per query that resolves people, plus the system methods module auth uses (4.2)."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import DomainError
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType, TargetScope
from src.modules.property.service import PropertyService
from tests.factories import DemoBuilding, create_demo_building, create_membership, create_user


@pytest.fixture
async def demo(db_session: AsyncSession) -> DemoBuilding:
    return await create_demo_building(db_session)


async def end(session: AsyncSession, membership) -> None:
    membership.ended_at = datetime.now(UTC) - timedelta(days=1)
    await session.flush()


async def test_an_ended_membership_grants_nothing(
    db_session: AsyncSession, demo: DemoBuilding, api_client: AsyncClient
) -> None:
    properties = PropertyService(db_session)
    await end(db_session, demo.owner_14_membership)

    current = await properties.get_current_user(demo.owner_14.id)
    assert current is not None and current.memberships == ()
    me = await api_client.get(
        "/api/v1/property/me", headers={"X-Demo-User-Id": str(demo.owner_14.id)}
    )
    assert me.json()["memberships"] == []
    privacy = await api_client.patch(
        f"/api/v1/property/memberships/{demo.owner_14_membership.id}/privacy",
        json={"privacy_display_mode": "FULL_NAME"},
        headers={"X-Demo-User-Id": str(demo.owner_14.id)},
    )
    assert privacy.json()["error_code"] == "MEMBERSHIP_NOT_FOUND"


async def test_recipients_and_submitters_skip_ended_memberships(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    await end(db_session, demo.tenant_2_membership)
    everyone = await properties.resolve_recipients(
        demo.building.id, TargetScope.ALL_BUILDING, None, ()
    )
    assert {r.user_id for r in everyone} == {demo.owner_14.id}
    submitters = await properties.reading_submitters([demo.apt_2.id, demo.apt_14.id])
    assert {r.user_id for r in submitters} == {demo.owner_14.id}


async def test_a_closed_account_has_no_delivery_e_mail(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    assert await properties.has_delivery_email(demo.owner_14.id)
    demo.owner_14.is_active = False
    await db_session.flush()
    assert not await properties.has_delivery_email(demo.owner_14.id)
    assert await properties.with_delivery_email([demo.owner_14.id]) == set()


async def test_an_ended_membership_shows_only_the_apartment(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    demo.owner_14_membership.privacy_display_mode = DisplayMode.FULL_NAME
    properties = PropertyService(db_session)
    badge = await properties.get_masked_author_badge(demo.owner_14.id, demo.apt_14.id)
    assert badge.display_label == "Ivan Petrov (Apt. 14)"
    await end(db_session, demo.owner_14_membership)
    badge = await properties.get_masked_author_badge(demo.owner_14.id, demo.apt_14.id)
    assert badge.display_label == "Apt. 14"  # nobody consented to the name there any more


async def test_residents_and_people_list_only_active_rows(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    await end(db_session, demo.tenant_2_membership)
    assert await properties.apartment_residents(demo.apt_2.id) == []
    people = await properties.building_people(demo.building.id)
    tenant = next(p for p in people if p.user_id == demo.tenant_2.id)
    assert tenant.memberships == ()  # still an account, without the ended flat
    assert people[0].user_id == demo.chairman.id  # accounts without a flat first


async def test_an_apartment_has_members_only_through_active_rows(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    assert await properties.has_active_members(demo.apt_2.id) is True
    closed = await create_user(db_session, demo.building, "Closed", is_active=False)
    await create_membership(db_session, closed, demo.apt_2, relation=RelationType.RESIDENT)

    await end(db_session, demo.tenant_2_membership)

    assert await properties.has_active_members(demo.apt_2.id) is False
    assert await properties.has_active_members(demo.apt_14.id) is True


async def test_poll_electors_are_active_rows_only(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    closed = await create_user(db_session, demo.building, "Closed", is_active=False)
    await create_membership(db_session, closed, demo.apt_14, relation=RelationType.RESIDENT)
    await end(db_session, demo.tenant_2_membership)

    residents = await properties.poll_electors(
        demo.building.id, TargetScope.ALL_BUILDING, None, [], owners_only=False
    )
    owners = await properties.poll_electors(
        demo.building.id, TargetScope.ENTRANCE, 2, [], owners_only=True
    )

    assert {(e.user_id, e.apartment_id) for e in residents} == {(demo.owner_14.id, demo.apt_14.id)}
    assert {(e.user_id, e.apartment_id) for e in owners} == {(demo.owner_14.id, demo.apt_14.id)}
    assert (
        await properties.poll_electors(
            demo.building.id, TargetScope.ENTRANCE, 1, [], owners_only=False
        )
        == []
    )


async def test_a_membership_is_reopened_not_duplicated(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    assert (
        await properties.open_membership(
            demo.owner_14.id, demo.apt_14.id, RelationType.OWNER, DisplayMode.APARTMENT_ONLY
        )
        is None
    )  # already active: nothing changes
    await end(db_session, demo.owner_14_membership)
    before = demo.owner_14_membership.started_at
    reopened = await properties.open_membership(
        demo.owner_14.id, demo.apt_14.id, RelationType.TENANT, DisplayMode.INITIALS
    )
    assert reopened == demo.owner_14_membership.id
    await db_session.refresh(demo.owner_14_membership)
    membership = demo.owner_14_membership
    assert membership.ended_at is None and membership.started_at > before
    assert (membership.relation_type, membership.can_vote) == (RelationType.TENANT, False)


async def test_the_last_chairman_cannot_go(db_session: AsyncSession, demo: DemoBuilding) -> None:
    properties = PropertyService(db_session)
    with pytest.raises(DomainError) as stepped_down:
        await properties.set_building_role(demo.chairman.id, BuildingRole.BOARD)
    assert stepped_down.value.error_code == "LAST_CHAIRMAN"
    with pytest.raises(DomainError):
        await properties.close_account(demo.chairman.id)
    second = await create_user(db_session, demo.building, "Second", role=BuildingRole.CHAIRMAN)
    await properties.set_building_role(demo.chairman.id, BuildingRole.BOARD)
    assert await properties.active_chairman_count(demo.building.id) == 1
    assert second.id != demo.chairman.id


async def test_closing_removes_the_personal_data(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    properties = PropertyService(db_session)
    extra = await create_membership(db_session, demo.owner_14, demo.apt_2)
    await db_session.flush()
    assert await properties.close_account(demo.owner_14.id) is True
    await db_session.refresh(demo.owner_14)
    user = demo.owner_14
    assert (user.is_active, user.email, user.phone, user.full_name) == (
        False,
        None,
        None,
        "Former resident",
    )
    await db_session.refresh(extra)
    assert extra.ended_at is not None
    assert await properties.inactive_user_ids([user.id, demo.tenant_2.id]) == {user.id}
    assert (await properties.building_roles([user.id]))[user.id] is None
    assert await properties.close_account(user.id) is False  # already closed
    # Readings keep the apartment, posts a masked badge: the history stays readable.
    badge = await properties.get_masked_author_badge(user.id, demo.apt_14.id)
    assert badge.display_label == "Apt. 14"
